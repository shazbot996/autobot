---
run: schiele/0072
at: 2026-11-27T06:07:26.984Z
mode: direct
agent: direct:fix
model: claude:claude-opus-5-5
target: null
template: direct/fix
prompt-sha: 260d78eedc5c
template-sha: 963e2e5821ed
commit: 2dc8acb+
attachments: []
read:
  - AGENTS.md
  - prompts/schiele/0071-okay-close-that-was-a-good-first-step.prompt.md
  - src/autobot/tui/echo.py
  - src/autobot/tui/face.py
  - src/autobot/config.py
  - src/autobot/tui/config_edit.py
  - src/autobot/tui/router.py
outcome: success
usage:
  input: 5466317
  output: 46502
  peak: 156105
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 61
  sandbox: true
  changed:
    - config/autobot.toml
    - docs/context/SCHEMA.md
    - src/autobot/config.py
    - src/autobot/routing.py
    - src/autobot/tui/echo.py
    - src/autobot/tui/face.py
    - src/autobot/tui/router.py
    - src/autobot/tui/screens/face.py
    - src/autobot/tui/screens/main.py
    - src/autobot/tui/screens/routing.py
    - tests/test_config.py
    - tests/test_routing.py
    - tests/tui/test_tui_app.py
    - tests/tui/test_tui_face_screen.py
    - tests/tui/test_tui_router.py
    - tests/tui/test_tui_routing_screen.py
---

"autobot" now only wakes the face. It then listens for a command for up to 10 seconds, and the echo runs only when it hears "echo", even inside a longer sentence. Routing is a small rules engine with its own **Agent routing** screen. The new tests pass, along with type check and lint. I haven't tried it on the NUC.

**How a session goes on the Face screen:**
1. **Wake.** You say "autobot". The listener still answers "Yes?". The summary reads `Listening for a command: say "echo".` and the eyes go to *listening*.
2. **Command.** The face checks what the listener transcribes against the rules. Words after "autobot" in the same breath count, so "autobot, echo that" routes at once. Anything said before the wake doesn't count.
3. **Echo.** "Could you echo my voice?" matches the `echo` rule. The detail panel shows which rule matched and what it heard, and the echo runs exactly as before.
4. **No match.** A sentence with no command is one detail line, and it keeps listening. After 10 seconds with nothing it says `No command heard. Say the wake phrase to try again.`

**The rules** live in `config/autobot.toml` as `[[routes]]` entries, each with a `command` and an `agent`. They're tried top to bottom and the first match wins. If the file has none, `echo` goes to the echo agent by default. Each new agent you add is one more entry here plus its agent name in the code.

**The Agent routing screen** is the entry after Face on the main menu. It has no number, so reach it with the arrow keys. It lists the rules, lets you change a rule's command, and has **Try a sentence...**, where you type a sentence and it shows which rule would take it.

**Checks:**
- **Tests:** 933 passed and 14 failed in `make test`, the same 14 as before. Thirteen need a Unix socket, which the sandbox blocks. The fourteenth fails because the committed config sets `theme = "sun"`.
- **Lint and type check:** `make typecheck` passes. `ruff check` and `ruff format --check` pass over `src` and `tests`. `make lint` itself still stops on `.claude/loop.md`, which the sandbox won't let it read.
- **New tests:**
  - `tests/test_routing.py`: how a command is matched.
  - `tests/test_config.py`: loading and checking `[[routes]]`.
  - `tests/tui/test_tui_router.py`: the listen-then-route flow.
  - `tests/tui/test_tui_routing_screen.py`: the new screen.
- **Updated tests:** the Face screen test now sends "please echo my voice" after the wake, and the menu tests include Agent routing.

**Limits:**
- **Commands need the current detector.** The face can only hear a command while the "autobot" wake phrase uses the `text_match` detector, as it does now. Once a trained model is installed, the listener publishes no words, and the face will say at once that no command can be heard. Fixing that means the listener transcribing the speech after a wake whatever the detector, which is a listener change I didn't make.
- **New rules are added by hand.** The screen can change a rule's command but can't add or remove rules, because the config editor only changes existing values. Edit `config/autobot.toml` for that. `echo` is the only valid agent so far.
- **The face spec is now out of date.** Rules FS-10 and FS-11 in `docs/specs/active/autobot-the-face-SPEC.md` still say the echo runs on every wake. I left the specs as they are, and the routing spec still needs your decisions on its open questions.

To try it on the NUC with `make up` running: open Face, say "autobot", wait for "Yes?", then say "echo that back".

Files changed:
- `src/autobot/config.py`: the `[[routes]]` table (`RouteConfig`, `ROUTE_AGENTS`), its checks, and the default echo rule.
- `src/autobot/routing.py` (new): matching a command and picking the first rule that matches.
- `src/autobot/tui/router.py` (new): what runs on a wake — listen for a command, start the agent, or end after 10 s.
- `src/autobot/tui/face.py`: collects what the listener hears during a session, starting with the words after the wake phrase.
- `src/autobot/tui/screens/face.py`: runs the router instead of the echo; new hint line.
- `src/autobot/tui/echo.py`: its closing line now says to say the wake phrase and "echo".
- `src/autobot/tui/screens/routing.py` (new): the Agent routing screen.
- `src/autobot/tui/screens/main.py`: the Agent routing menu entry and the new Face hint.
- `config/autobot.toml`: the `echo` rule, with a comment explaining routing.
- `docs/context/SCHEMA.md`: the `[[routes]]` row and the rule's fields.
- `tests/test_routing.py`, `tests/tui/test_tui_router.py`, `tests/tui/test_tui_routing_screen.py` (new): tests for the rules, the router and the screen.
- `tests/test_config.py`: tests for `[[routes]]`.
- `tests/tui/test_tui_face_screen.py`: a wake now only listens; the full echo needs "echo".
- `tests/tui/test_tui_app.py`: the menu tests include Agent routing.
