"""Regenerate the placeholder fixture clips. See README.md beside this file.

    .venv/bin/python tests/fixtures/audio/synthesise.py
    .venv/bin/python tests/fixtures/audio/synthesise.py --only yo_autobot

The speech clips are synthesised locally with piper-tts voices from
`models/piper/` (none of them the acknowledgement's voice), resampled to
16 kHz mono, padded with silence and mixed with low seeded noise.
`room_noise.wav` is synthetic too: rumble, mains hum, hiss and two clicks.
Nothing here opens a microphone or the network.

This is a tool, not a test: pytest does not collect it and the tests never
run it.
"""

from __future__ import annotations

import argparse
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from autobot.listener.models import enforce_offline, piper_voice_path

HERE = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
LEAD_SECONDS = 0.8
TAIL_SECONDS = 1.2
# Standard deviation of the added noise, in int16 units (about -54 dBFS).
NOISE_LEVEL = 60.0
ROOM_SECONDS = 3.0


@dataclass(frozen=True)
class Clip:
    name: str
    voice: str
    text: str
    length_scale: float = 1.0


CLIPS = (
    Clip("autobot", "en_US-ryan-medium", "Autobot."),
    Clip("hey_autobot", "en_US-amy-medium", "Hey, autobot."),
    Clip("yo_autobot", "en_US-hfc_male-medium", "Yo, autobot.", 1.1),
    Clip(
        "unrelated_speech",
        "en_US-kristin-medium",
        "The weather tomorrow looks fine for a walk in the park.",
    ),
    Clip("near_miss_auto", "en_US-joe-medium", "Auto."),
)


def synthesise(clip: Clip) -> npt.NDArray[np.int16]:
    """The clip's speech at 16 kHz, without padding or noise."""
    import av
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(piper_voice_path(clip.voice))
    chunks = voice.synthesize(
        clip.text, SynthesisConfig(length_scale=clip.length_scale)
    )
    samples = np.concatenate([chunk.audio_int16_array for chunk in chunks])
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(
        samples.reshape(1, -1), format="s16", layout="mono"
    )
    frame.sample_rate = voice.config.sample_rate
    pieces = [*resampler.resample(frame), *resampler.resample(None)]
    return np.concatenate([piece.to_ndarray().reshape(-1) for piece in pieces])


def pad_and_mix(
    speech: npt.NDArray[np.int16], rng: np.random.Generator
) -> npt.NDArray[np.int16]:
    lead = np.zeros(int(LEAD_SECONDS * SAMPLE_RATE), dtype=np.int16)
    tail = np.zeros(int(TAIL_SECONDS * SAMPLE_RATE), dtype=np.int16)
    padded = np.concatenate([lead, speech, tail]).astype(np.float64)
    noisy = padded + rng.normal(0.0, NOISE_LEVEL, len(padded))
    return np.clip(np.round(noisy), -32768, 32767).astype(np.int16)


def write_wav(path: Path, samples: npt.NDArray[np.int16]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.tobytes())
    print(f"wrote {path.name}: {len(samples) / SAMPLE_RATE:.2f} s")


def room_noise(rng: np.random.Generator) -> npt.NDArray[np.int16]:
    """Three seconds of a quiet room: low rumble, mains hum, hiss and two clicks."""
    count = int(ROOM_SECONDS * SAMPLE_RATE)
    time = np.arange(count) / SAMPLE_RATE
    rumble = np.cumsum(rng.normal(0.0, 1.0, count))
    rumble -= np.linspace(rumble[0], rumble[-1], count)
    rumble *= 150.0 / max(1.0, float(np.abs(rumble).max()))
    hum = 40.0 * np.sin(2 * np.pi * 60.0 * time) + 15.0 * np.sin(
        2 * np.pi * 120.0 * time
    )
    hiss = rng.normal(0.0, NOISE_LEVEL, count)
    room = rumble + hum + hiss
    for at in (0.9, 2.1):
        start = int(at * SAMPLE_RATE)
        room[start : start + 80] += 2500.0 * np.exp(-np.arange(80) / 12.0)
    return np.clip(np.round(room), -32768, 32767).astype(np.int16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="regenerate one clip by name")
    args = parser.parse_args()
    enforce_offline()
    for number, clip in enumerate(CLIPS):
        if args.only and clip.name != args.only:
            continue
        rng = np.random.default_rng(20261125 + number)
        write_wav(HERE / f"{clip.name}.wav", pad_and_mix(synthesise(clip), rng))
    if not args.only or args.only == "room_noise":
        write_wav(HERE / "room_noise.wav", room_noise(np.random.default_rng(20261124)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
