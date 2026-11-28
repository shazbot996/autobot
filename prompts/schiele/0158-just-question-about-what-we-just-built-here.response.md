---
run: schiele/0158
at: 2026-11-28T14:07:50.034Z
mode: direct
agent: direct:ask
model: claude:claude-fable-5-1
target: null
template: direct/ask
prompt-sha: a0cf1a22dd50
template-sha: 44af0dfef32e
commit: 44a5f3e
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-edge-control-plane-SPEC.md
  - src/autobot/tui/menu.py
  - src/autobot/tui/app.py
  - docs/specs/active/autobot-the-face-SPEC.md
  - src/autobot/tui/screens/main.py
outcome: success
usage:
  input: 290853
  output: 3561
  peak: 58658
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 10
  sandbox: false
---

Short answer: yes. The spec keeps everything inside the one terminal application plus the `Makefile`, and adds no second control panel. But it has one gap against what you just said (reflexes are shown, not edited), and the main menu is already at its ceiling, so the next build step has to reorganise it rather than append to it.

## What the spec already commits to

- **Everything is shown in the game interface.** EC-19 puts the reflexes on the face's detail panel (phrase, action, asks-first), shows each reflex heard with its measured time and open confirmation window, and says no behaviour of this layer may be invisible there.
- **The tunables are TUI settings.** EC-9 makes the shared confirmation window and the session idle time "obvious, easy-to-find settings in the configuration screens of the game interface", and says the owner turns `confirm = false` "from the configuration interface".
- **One config file.** EC-6 puts reflexes in `[[reflexes]]` in `config/autobot.toml`, beside `[[routes]]` and `[[agents]]`, which is what the existing `ConfigStore` (`src/autobot/tui/config_edit.py`) writes. EC-3 reuses `[audio] input_device` / `output_device`, which the Audio screen already edits.
- **One backend process, reached the same way.** EC-2 makes the listener the only capture process and has the interface get levels, transcripts and the `reflex` event through `run/listener.sock` and `python -m autobot.listener.control`, exactly as today. `src/autobot/tui/router.py` is in the spec's `describes` list, so routing changes land in the TUI's own module.
- **Hardware check is a `make` target.** EC-4's `make echo-check` sits beside `make audio-check`.
- **The console is explicitly out of scope.**

The only "new service" is PipeWire's echo-cancel module, which is sound-server configuration on the NUC, not an application.

## The gap you should know about

Under "Settled without asking" the spec says: *"For now its entries are edited in the file, and the game interface shows them (EC-19) without editing them."* That contradicts EC-9's "from the configuration interface" and your stated intent. If you want reflexes configurable from the TUI, the spec needs a **Reflexes** editor (phrase, action from a fixed list, confirm, question), most naturally beside Agent routing, since both are "what a phrase does after the wake". Related: there is no `[server]` screen today either; `src/autobot/tui/screens/main.py` only shows the server's state in the banner (IS-23), so `stt_enabled` and the server address are still file-only.

## The crowding problem is real

`MainMenu.items()` in `screens/main.py` has 14 entries (13 screens + Quit), and the code comment at `draw()` already notes that at the minimum terminal height "three rows under the menu's fourteen entries" are all that is left for the state panel. There is no room to append Reflexes, Timing and Inference server. The tagline still reads "wake-word training studio", which no longer describes the application.

A grouping that matches the existing modules, for the face/studio spec revision:

- **Studio**: Recording session, Train, Candidate, Live monitor, Dataset
- **Audio & listener**: Audio (with echo-cancel nodes and `echo-check` result), Wake phrase, Service, Host
- **Agents**: Agent editor, Agent routing, Reflexes, Word lists, Session timing (the two EC-9 windows)
- **Connections**: Google Cloud, Inference server

That is a one-level grouping change in `MainMenu`, not a new application, and the `MenuItem` hints already carry the one-line descriptions a submenu needs.

## On the GPU-server interface, when you get to it

The NUC TUI should stay the one place autobot's *behaviour* is configured. A `GAMEON1` interface should be an operator view of that machine (models, jobs, slots, logs), and anything that changes how autobot acts still goes through `config/autobot.toml` on the NUC. Worth writing that boundary into the next spec so it does not become the second control panel you do not want.
