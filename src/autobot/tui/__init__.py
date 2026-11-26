"""The terminal user interface that `make deploy` opens.

A 90s-style full-screen `curses` front for `config/autobot.toml` and for the
listener's control commands. It is a control layer below the application
(AGENTS.md, "Runtime control and audio checks"): it holds no configuration of
its own, and every change it makes is written back to the config file.

Everything that can be tested without a terminal lives outside the drawing
code: `config_edit` (writing the config file), `audio` (levels, devices,
clips), `pipewire` (default source and sink), `runner` (subprocesses) and
`menu` (the menu model). `app` and `screens/` draw them.
"""
