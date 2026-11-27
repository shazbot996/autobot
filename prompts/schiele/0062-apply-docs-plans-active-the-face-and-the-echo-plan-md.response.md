---
run: schiele/0062
at: 2026-11-27T05:32:29.104Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: a5c07501e96f
template-sha: bf7cd0cb8062
commit: 45a089a+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/face_ticker.py
  - tests/tui/test_tui_face_ticker.py
outcome: success
usage:
  input: 714783
  output: 31346
  peak: 85432
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 11
  sandbox: true
  changed:
    - src/autobot/tui/face_reactions.py
    - tests/tui/test_tui_face_reactions.py
---

## Result

I applied step 5 of 9. Steps 1 to 4 were already in place from earlier runs, and steps 6 to 9 were outside this run's scope. The 30 new tests pass, and so do `make typecheck`, `ruff check` and `ruff format --check`. `make test` still exits with an error: 14 tests fail, the same 14 that failed before this step.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here
### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here
### 3. The interface's plumbing: argv, the wake colour, the voice seam: done in run schiele/0058, not touched here
### 4. The ticker model: done in run schiele/0060, not touched here

### 5. Eyes and reactions as data: done, verified
`src/autobot/tui/face_reactions.py` is new and does no drawing to the screen.

- **`Pose`:** has `across` (left, center or right), `level` (up, middle or down), `lid` (open, wide, half or closed) and `hold` in milliseconds. A fixed hold is stored as a range with both ends equal.
- **Reactions in data:** a pose is written like `{"pupil": "up left", "lid": "half", "hold": [800, 3000]}`. A reaction is `{"poses": [...], "loop": bool, "random": bool}`.
- **`reaction_set`:** rejects bad data with a one-line `ReactionError` that names the reaction and the pose number. That covers unknown pupil or lid words, unknown keys, a missing or bad hold, and an empty pose list.
- **`BUILTIN_REACTIONS`:** a plain dict defining the seven built-in reactions.
  - *idle* is random and loops. It has seven entries: centre twice, left, right, up, down and a short blink. Glances are held 0.8 to 3 s.
  - A random reaction never shows the same entry twice running. With seven entries, a blink is about one in six poses.
- **`Eyes(clock, random)`:**
  - `play(name, reactions)` uses the experience's set and falls back to the built-in set for any name the set leaves out.
  - A reaction that doesn't loop holds its last pose for that pose's time, then goes back to *idle*. That is the set's own *idle* if it has one.
  - `pose()` moves on from any pose whose time is up.
- **`draw_eyes(pose, glyphs)`:** returns four equal-width rows of two identical eyes, each with three columns and two rows inside.
  - It uses rounded line-drawing characters and block pupils when `glyphs.block_art` is set, and `.---.`, `( o )`, `'---'` otherwise.
  - A middle pupil spans both inside rows. A wide lid shows a smaller pupil. A half lid draws a lid line across the top row. A closed lid is a single arc. There are no eyebrows.

What confirmed it: `tests/tui/test_tui_face_reactions.py` (new) has 30 tests, all passing. They check each item of the "Done when":
- With seed 7 and a fake clock, *idle* shows at least three pupil positions and a closed lid within 30 s.
- `play("wake")` changes the pose on the very next `pose()` call.
- A set that defines only *wake* still plays the built-in *done*, and then the built-in *idle*.
- Every ASCII drawing is printable ASCII.

The other tests cover the data errors, glance and blink lengths, looping, unknown names, and that every pupil position and lid draws differently.

Checks:
- **`make typecheck`:** no issues in 86 source files.
- **`ruff check src tests`:** passes. **`ruff format --check src tests`:** 87 files already formatted.
- **`make test`:** 832 passed, 14 failed. Thirteen of the failures are tests that need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.

**Three choices the plan didn't settle:**
- **Unknown reaction name:** `play` on a name that neither set defines plays *idle* instead of raising an error. That way a typo in an experience can't crash the interface.
- **Idle test depends on its seed:** across 200 seeds, 17 don't meet the 30-second idle condition, because the blink is random. The test uses a fixed seed (7), so it gives the same result every run. If the owner wants the 30-second property to hold for any seed, *idle* would need a guaranteed blink rather than a random one.
- **Wide lid in ASCII:** a smaller pupil on a full eye is how "wide" is drawn. In ASCII the centred wide pupil (`.`) is only a little smaller than the open one (`.` over `'`), so the difference is subtle there. It's worth a look on the NUC once the screen exists.

### 6. The meters: not started (out of scope)
### 7. The panels and the experience frame: not started (out of scope)
### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/face_reactions.py` (new): `Pose`, `Reaction`, `reaction_set`, `ReactionError`, `BUILTIN_REACTIONS`, the `Eyes` animator and `draw_eyes`.
- `tests/tui/test_tui_face_reactions.py` (new): 30 tests of the reaction data, the animator with a fake clock and seeded random, and both drawings.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the socket tests and the full lint target pass.
