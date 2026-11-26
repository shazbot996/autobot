"""The spoken acknowledgement: "Yes?" from the speaker on every wake.

`render` synthesises `[ack].text` once with piper-tts, locally
(CONSTRAINTS.md CN-2), from the voice file `models/piper/<voice>.onnx`, into
memory. `Acknowledgement.play` plays that clip on the configured output
device. Rendering once keeps the response immediate and keeps the always-on
loop free of synthesis work.

`python -m autobot.listener.ack` renders the acknowledgement, plays it once
and exits; the terminal user interface runs it as a subprocess. Like every
other audio command it runs on the NUC only.

`sounddevice` and `piper` are imported inside the functions that need them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from autobot.audio_check import AudioCheckError, resolve_device
from autobot.config import AckConfig, ConfigError, DeviceSpec, load_config
from autobot.listener.audio import AudioError, require_nuc
from autobot.listener.models import ModelMissingError, enforce_offline, piper_voice_path

# Silence after the speech, so the output device does not cut the last sound.
TAIL_SECONDS = 0.15


@dataclass(frozen=True)
class AckClip:
    """The rendered acknowledgement: mono int16 samples at the voice's rate."""

    samples: npt.NDArray[np.int16]
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate


def render(ack: AckConfig) -> AckClip:
    """Synthesise the acknowledgement text in the configured voice."""
    model = piper_voice_path(ack.voice)
    enforce_offline()
    from piper import PiperVoice

    voice = PiperVoice.load(model)
    chunks = [chunk.audio_int16_array for chunk in voice.synthesize(ack.text)]
    rate = int(voice.config.sample_rate)
    tail = np.zeros(int(TAIL_SECONDS * rate), dtype=np.int16)
    return AckClip(samples=np.concatenate([*chunks, tail]), sample_rate=rate)


class Acknowledgement:
    """Plays the rendered clip on the configured output device."""

    def __init__(self, clip: AckClip, output_device: DeviceSpec):
        self.clip = clip
        self._output_device = output_device

    def play(self) -> None:
        """Play the clip and return when it has finished."""
        import sounddevice as sd

        try:
            # Resolved on every play: device indexes move when hardware is plugged in.
            index = resolve_device(
                self._output_device, "output", list(sd.query_devices())
            )
            sd.play(
                self.clip.samples,
                samplerate=self.clip.sample_rate,
                device=index,
                blocking=True,
            )
        except AudioCheckError as error:
            raise AudioError(str(error)) from None
        except sd.PortAudioError as error:
            raise AudioError(f"cannot play on the output device: {error}") from None


def main() -> int:
    try:
        config = load_config()
        require_nuc(config, "the acknowledgement")
        clip = render(config.ack)
        Acknowledgement(clip, config.audio.output_device).play()
    except (ConfigError, AudioError, ModelMissingError) as error:
        print(error, file=sys.stderr)
        return 1
    print(
        f"ack spoken: {config.ack.text!r} in voice {config.ack.voice} "
        f"({clip.seconds:.1f} s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
