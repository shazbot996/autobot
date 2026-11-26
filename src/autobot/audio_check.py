"""`make audio-check`: check the NUC's local audio hardware.

Refuses to run on any host other than `host.nuc_hostname`. On the NUC it lists
the audio devices, records two seconds from the configured input, prints the
peak and RMS level, and plays the recording back on the configured output.

`resolve_device` is the one place a configured device (name substring or
index) is turned into a `sounddevice` index; the listener reuses it.

`sounddevice` is imported inside the functions that touch hardware, so that
importing this module never loads PortAudio.
"""

from __future__ import annotations

import math
import socket
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from autobot.config import Config, ConfigError, DeviceSpec, load_config

RECORD_SECONDS = 2.0
INT16_FULL_SCALE = 32768.0

type DeviceKind = Literal["input", "output"]


class AudioCheckError(Exception):
    """The audio check cannot run or failed. The message is one line."""


def check_host(config: Config, hostname: str) -> None:
    """Raise unless `hostname` is the configured NUC."""
    nuc = config.host.nuc_hostname
    if not nuc or hostname.lower() != nuc.lower():
        raise AudioCheckError(
            f"audio-check runs on the NUC only; this host is {hostname}"
        )


def resolve_device(
    spec: DeviceSpec, kind: DeviceKind, devices: Sequence[Mapping[str, Any]]
) -> int:
    """Return the index in `devices` of the device `spec` names.

    `devices` is `sounddevice.query_devices()` (each entry has `name`,
    `max_input_channels` and `max_output_channels`). `spec` is an index, or a
    case-insensitive substring of a device name; only devices with channels of
    the wanted `kind` are considered, and an exact name wins over a substring.
    """
    channels_key = f"max_{kind}_channels"
    if isinstance(spec, int):
        if not 0 <= spec < len(devices):
            raise AudioCheckError(f"{kind} device index {spec} does not exist")
        if devices[spec][channels_key] <= 0:
            raise AudioCheckError(
                f"device {spec} ({devices[spec]['name']}) has no {kind} channels"
            )
        return spec
    wanted = spec.strip().lower()
    usable = [
        (index, str(device["name"]).lower())
        for index, device in enumerate(devices)
        if device[channels_key] > 0
    ]
    exact = [index for index, name in usable if name == wanted]
    matches = exact or [index for index, name in usable if wanted in name]
    if not matches:
        raise AudioCheckError(f"no {kind} device matches {spec!r}")
    if len(matches) > 1:
        names = ", ".join(str(devices[index]["name"]) for index in matches)
        raise AudioCheckError(
            f"{kind} device {spec!r} is ambiguous: it matches {names}"
        )
    return matches[0]


def levels(samples: Sequence[int]) -> tuple[int, float]:
    """Peak and RMS of int16 samples, in sample units."""
    if not samples:
        return 0, 0.0
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return peak, rms


def dbfs(level: float) -> str:
    """A level in sample units as dB relative to int16 full scale."""
    if level <= 0:
        return "-inf dBFS"
    return f"{20 * math.log10(level / INT16_FULL_SCALE):.1f} dBFS"


def run_check(config: Config) -> None:
    """List devices, record, report levels and play back. Opens real hardware."""
    import sounddevice as sd

    try:
        devices = list(sd.query_devices())
        print("Audio devices (index, name, input/output channels):")
        for index, device in enumerate(devices):
            print(
                f"  {index:>2}  {device['name']}  "
                f"({device['max_input_channels']} in, {device['max_output_channels']} out)"
            )
        input_index = resolve_device(config.audio.input_device, "input", devices)
        output_index = resolve_device(config.audio.output_device, "output", devices)
        rate = config.audio.sample_rate
        print(
            f"Input:  {config.audio.input_device!r} -> {input_index} {devices[input_index]['name']}"
        )
        print(
            f"Output: {config.audio.output_device!r} -> {output_index} "
            f"{devices[output_index]['name']}"
        )

        print(f"Recording {RECORD_SECONDS:g} s at {rate} Hz mono: speak now...")
        recording = sd.rec(
            int(RECORD_SECONDS * rate),
            samplerate=rate,
            channels=1,
            dtype="int16",
            device=input_index,
        )
        sd.wait()
        peak, rms = levels(recording[:, 0].tolist())
        print(f"Peak: {peak} of 32768 ({dbfs(peak)})")
        print(f"RMS:  {rms:.1f} ({dbfs(rms)})")

        print("Playing the recording back...")
        sd.play(recording, samplerate=rate, device=output_index)
        sd.wait()
    except sd.PortAudioError as error:
        raise AudioCheckError(f"audio device error: {error}") from None

    if peak == 0:
        raise AudioCheckError(
            "the recording is pure silence: check the input device and its mute"
        )
    print("audio-check done.")


def main() -> int:
    try:
        config = load_config()
        check_host(config, socket.gethostname())
        run_check(config)
    except (ConfigError, AudioCheckError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
