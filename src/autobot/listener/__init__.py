"""The wake-word listener: the always-on local process on the NUC.

`python -m autobot.listener` runs the service (`service.py`). The parts:

- `audio.py`     microphone frames (16 kHz mono int16, 80 ms each)
- `vad.py`       Silero VAD and the speech gate that keeps silence from detectors
- `detector.py`  the `Detector` protocol, `WakeEvent`, and the kind -> engine factory
- `engines/`     one module per wake phrase kind (`text_match`, later `openwakeword`)
- `events.py`    `WakeEvent` JSON lines to the log and to `run/listener.sock`
- `ack.py`       the spoken acknowledgement (piper-tts, rendered once)
- `control.py`   `make up` / `down` / `status` / `logs` and the systemd unit
- `models.py`    local model files and the no-network guard

Everything here runs locally and offline: CONSTRAINTS.md CN-1 and CN-2.
"""
