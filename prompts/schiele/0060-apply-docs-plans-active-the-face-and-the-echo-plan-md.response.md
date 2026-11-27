---
run: schiele/0060
at: 2026-11-27T05:28:19.554Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: f4fd428b73bd
template-sha: bf7cd0cb8062
commit: b4431e3+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/runner.py
  - src/autobot/tui/ui.py
  - src/autobot/tui/themes.py
  - src/autobot/tui/app.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_themes.py
  - src/autobot/tui/face_voice.py
outcome: success
usage:
  input: 809719
  output: 20322
  peak: 90175
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 16
  sandbox: true
  changed:
    - src/autobot/tui/face_ticker.py
    - tests/tui/test_tui_face_ticker.py
---

## Result

I applied step 4 of 9. Steps 1 to 3 were already in place from earlier runs, and steps 5 to 9 were outside this run's scope. The 29 new tests pass, and so do `make typecheck`, `ruff check` and the format check. `make test` still exits with an error: 14 tests fail, the same 14 that failed before this step.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here
### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here
### 3. The interface's plumbing: argv, the wake colour, the voice seam: done in run schiele/0058, not touched here

### 4. The ticker model: done, verified
`src/autobot/tui/face_ticker.py` is new and does no drawing.

- **`Ticker.feed(events)`:**
  - A `transcript` becomes an item, with its whitespace collapsed. Empty texts are skipped.
  - An `openwakeword` or `sherpa_kws` score line becomes one marker per stretch, but only if that stretch has no transcript. The marker is the glyph the screen passes in (`glyphs.bullet`), or `*` by default.
  - A `wake` pops the newest item that has its phrase and isn't popped yet, in the current stretch. The current stretch is the one named by the last score or transcript line, because a wake line carries no stretch number.
- **`phrase_span(text, phrase)`:** finds the phrase in the text using the listener's containment rule (lower case, apostrophes dropped, a match only at a word start). It returns positions in the original text, so "auto-bot" or "Autobot" is the part that pops. When nothing matches, the whole item pops.
- **`advance()`:** moves the strip one column per frame, and two while more than half a width is waiting. A feed that would leave the strip more than one width behind skips ahead. Text that has scrolled off the left is dropped.
- **`view(width)`:** returns exactly `width` cells (`Cell(char, wake)`).
- **`muted`:** while it is set, every incoming event is dropped.
- **`down`:** while it is set, the view shows `The listener is down: nothing is being heard.` and doesn't scroll. Going down also clears the strip.

What confirmed it: `tests/tui/test_tui_face_ticker.py` (new) has 29 tests, all passing. They check each item of the "Done when":
- "hey autobot" scrolls in from the right one column per frame, and only its `autobot` cells are tagged wake.
- A `sherpa_kws` stretch shows one marker, and its wake pops the newer stretch's marker.
- Nothing is added while muted.
- The down sentence appears and doesn't scroll.
- After a long feed, and through 200 frames after it, the backlog stays within one width.

The other tests cover `phrase_span` cases, words taking precedence over markers, and that the ticker creates no file.

Checks:
- **`make typecheck`:** no issues in 84 source files.
- **`ruff check src tests`:** passes. **`ruff format --check src tests`:** 85 files formatted.
- **`make test`:** 802 passed, 14 failed. Thirteen of the failures are tests that need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.

**Three things the plan didn't spell out:**
- **Restarted listener:** stretch numbers start again at 1, so a lower number than the last one detaches the older items. A wake then can't pop an item from the earlier run.
- **Several detectors in one stretch:** two `text_match` phrases that write the same words in a stretch share one item. Two marker phrases in one stretch share one marker.
- **No imports from the listener:** the containment rule is re-implemented in this module, as plan choice 8 asks, because the interface never imports the listener.

### 5. Eyes and reactions as data: not started (out of scope)
### 6. The meters: not started (out of scope)
### 7. The panels and the experience frame: not started (out of scope)
### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/face_ticker.py` (new): the `Ticker` model, `Cell` and `phrase_span`.
- `tests/tui/test_tui_face_ticker.py` (new): 29 tests of the ticker and `phrase_span`, fed recorded event lists.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the socket tests and the full lint target pass.
