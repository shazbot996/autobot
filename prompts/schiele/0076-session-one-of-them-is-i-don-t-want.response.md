---
run: schiele/0076
at: 2026-11-27T06:25:01.843Z
mode: direct
agent: direct:fix
model: claude:claude-opus-5-5
target: null
template: direct/fix
prompt-sha: 738b24e41ef9
template-sha: 963e2e5821ed
commit: c1e755e+
attachments: []
read:
  - AGENTS.md
outcome: success
usage:
  input: 1728942
  output: 22695
  peak: 81199
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 30
  sandbox: true
  changed:
    - AGENTS.md
    - CHANGELOG.md
    - Makefile
    - README.md
    - config/autobot.toml
    - docs/context/ARCH.md
    - docs/context/CONVENTIONS.md
    - docs/context/GLOSSARY.md
    - docs/context/INFRASTRUCTURE.md
    - docs/specs/active/autobot-the-face-SPEC.md
    - docs/specs/active/autobot-wake-word-training-studio-SPEC.md
    - src/autobot/tui/__init__.py
    - src/autobot/tui/__main__.py
    - src/autobot/tui/app.py
    - src/autobot/tui/face.py
    - src/autobot/tui/face_ticker.py
    - src/autobot/tui/screens/face.py
    - src/autobot/wakeword/train.py
    - tests/fixtures/audio/README.md
    - tests/tui/test_tui_face.py
    - tests/tui/test_tui_face_screen.py
    - tests/tui/test_tui_face_ticker.py
    - tests/tui/tui_fakes.py
---

I've made both changes: `make launch` now opens the application, and the transcript ticker at the bottom of the Face screen should scroll smoothly. I haven't watched it in a real terminal yet, so it's worth opening the Face screen on the NUC to see if it reads well.

## 1. `make deploy` is now `make launch`
The target is renamed and `make deploy` no longer exists, so the name is free for cloud deployments. A bare `make` lists `launch` and its last line now reads "`make launch` opens the application". I also updated every mention that describes how things work today: the docs, both active specs, the config comments, the fixtures README and the messages the code prints.

I left the decision records alone, because those are never edited, and the older entries in the plans and the changelog, because they describe history. I added a changelog entry for the rename.

## 2. The ticker scrolls smoothly
**Why it was jumpy:** the ticker moved one character each time the screen redrew. Redraws came about every 100 ms, but a key press made one come early, and slow work made one come late, so the steps were uneven. On top of that, once a sentence was waiting to come in, it jumped two characters at a time.

**What I changed:** a terminal can't move text by less than one character, so the smoothest it can do is one character at a time at even intervals.
- The ticker now moves at a steady 16 characters a second by the clock, never more than one character per redraw. A late redraw is not made up with a jump.
- The Face screen schedules its next redraw for the moment the next character is due, instead of waiting up to 100 ms.
- While a sentence is waiting, the speed rises gradually to at most twice normal, then eases back down as the backlog clears.

The old safeguard stays: it never falls more than one screen-width behind.

If it reads too fast or too slow, the speed is one number, `SPEED` in `src/autobot/tui/face_ticker.py`. `CATCH_UP` sets how much faster it goes while text is waiting.

## Checks
- **Type check:** passes.
- **Lint:** the lint itself passes, and so does the format check over `src` and `tests`. `make lint` still exits with an error, because the sandbox won't let the format check read `.claude/loop.md`.
- **Tests:** 14 of 949 fail, none because of these changes:
  - 13 need a Unix socket, which the sandbox refuses.
  - 1, `test_the_committed_config_is_a_fresh_install`, fails because `config/autobot.toml` has `theme = "sun"` where the test expects `"light"`. I only touched comments in that file.

Files changed:
- `Makefile`: target renamed to `launch`, plus the summary line and a comment.
- `src/autobot/tui/face_ticker.py`: ticker moves by the clock, one character at a time, with gradual speed-up; reports when the next character is due.
- `src/autobot/tui/face.py`: passes the clock to the ticker and says when the next redraw is wanted.
- `src/autobot/tui/screens/face.py`: schedules the Face screen's redraws by the ticker.
- `src/autobot/tui/app.py`: lets a screen ask for its next redraw sooner (capped at 100 ms); the no-terminal message says `make launch`.
- `src/autobot/tui/__init__.py`, `src/autobot/tui/__main__.py`, `src/autobot/wakeword/train.py`: docstrings and messages say `make launch`.
- `tests/tui/test_tui_face_ticker.py`: tests drive a fake clock; new tests for single steps, the redraw timing and the gradual speed-up.
- `tests/tui/test_tui_face.py`, `tests/tui/test_tui_face_screen.py`: run on a fake clock; one checks that the Face screen asks for faster redraws.
- `tests/tui/tui_fakes.py`: the fake screen accepts the redraw-wait setting.
- `README.md`, `AGENTS.md`, `docs/context/ARCH.md`, `docs/context/CONVENTIONS.md`, `docs/context/INFRASTRUCTURE.md`, `docs/context/GLOSSARY.md`: `make deploy` → `make launch`.
- `docs/specs/active/autobot-the-face-SPEC.md`, `docs/specs/active/autobot-wake-word-training-studio-SPEC.md`: `make deploy` → `make launch`.
- `config/autobot.toml`, `tests/fixtures/audio/README.md`: comments say `make launch`.
- `CHANGELOG.md`: two entries under Unreleased → Changed, for the rename and the smoother ticker.
