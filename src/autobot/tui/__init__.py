"""The application: the full-screen terminal user interface `make deploy` opens.

This interface is autobot's application, not a control panel beside one. Its
first job is the wake-word training studio: collecting the owner's voice on
the NUC, training the `autobot` model from it, and installing the result. The
settings screens (audio, wake phrase, service, host) are part of it.

It is drawn full screen with `curses` and stays a front: it holds no
configuration of its own, every change it makes is written back to
`config/autobot.toml`, and it reaches the listener only through its control
commands.

Everything that can be tested without a terminal lives outside the drawing
code: `config_edit` (writing the config file), `audio` (levels, devices,
clips, the recorder), `session` (the recording session), `state` (where the
listener stands between untrained and installed), `themes` (the colour
palettes), `pipewire` (default source and sink), `runner` (subprocesses) and
`menu` (the menu model). `app` and `screens/` draw them. The voice dataset
and the recording script are `autobot.wakeword.dataset` and
`autobot.wakeword.script`.
"""
