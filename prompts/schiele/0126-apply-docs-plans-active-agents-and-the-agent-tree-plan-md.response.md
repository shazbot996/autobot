---
run: schiele/0126
at: 2026-11-27T18:44:04.900Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/agents-and-the-agent-tree-PLAN.md
template: apply
prompt-sha: a65274454247
template-sha: bf7cd0cb8062
commit: db4b308+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/agents-and-the-agent-tree-PLAN.md
  - src/autobot/agents/tree.py
  - tests/agents/test_agent_tree.py
  - src/autobot/tui/router.py
  - src/autobot/tui/face.py
  - tests/tui/test_tui_router.py
  - src/autobot/tui/face_reactions.py
  - src/autobot/tui/ui.py
outcome: success
usage:
  input: 678760
  output: 18838
  peak: 91199
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 12
  sandbox: true
  changed:
    - config/autobot.toml
    - src/autobot/tui/tree_game.py
    - tests/tui/test_tui_tree_game.py
---

## Result

I applied step 5 of 13, the only step in scope. Steps 1 to 4 were already done, and steps 6 to 13 were not started.

The new tests pass (21 of them), and so do `make typecheck`, `ruff check src tests` and `ruff format --check src tests`. `make test` gives 1189 passed and 14 failed. Those are the same 14 that have failed since step 1, and this change didn't cause them: 13 socket tests the sandbox refuses with `PermissionError`, and `test_the_committed_config_is_a_fresh_install`. I ran ruff on `src` and `tests` rather than `make lint`, because last run the sandbox stopped `ruff format --check .` from reading `.claude/loop.md`.

## Steps

### 1 to 4: already done before this run

### 5. The game screen's movement: done, verified
`src/autobot/tui/tree_game.py` is new and does no drawing:
- **`Position(level, node, phase)`**: the six phases are module constants. It defaults to the root, listening.
- **`Game(picture)`**: the protagonist travels to the place a position names, along the tree's lines. Each move is one cell, and the whole travel takes `TRAVEL_SECONDS = 0.4` whatever its length, computed from the clock each frame. A change in the middle of a travel starts the next one from the cell he stands on, and he takes the shortest way back along the tree. Nothing waits or sleeps.
- **`draw_protagonist(pose, glyphs)`**: two rows, 8 wide. In ASCII he is `.-..-.` over `(o)(o)`; in Unicode `╭─╮╭─╮` over `│█││█│`. The pupil changes with the lid and the level, and the pair moves a column towards `pose.across`.

The tests use the committed config's picture (ASCII and Unicode) and a fake clock. They show:
- at `listening` the offset centres the root;
- after `wake` he is between the levels at 0.2 s and on the agents' level at 0.4 s;
- after `command weather` he reaches the weather box and `highlight == "weather"`;
- `running` opens the level view, and `done` returns him to the root;
- a change mid-travel starts from his current cell, and no frame moves him more than one travel's length.

Where I departed from the plan's wording:
- **`offset(now, rows, cols)`** takes the panel's size, because "the cell that puts him at the panel's centre" depends on it. The plan wrote `offset(now)`.
- **`highlight(now)` and `level_view(now)`** are methods that take the time, not plain attributes, because both depend on whether the travel has ended.
- **`level_view` waits for him to reach the node.** It turns on only once the phase is `running` *and* he has arrived. Otherwise an agent that starts at once, like the echo, would never show the slide to its node that AG-3 describes. The session itself still doesn't wait on this.
- **Where he stands before a command:** after a wake and before a command he goes straight down from the root to the agents' row. On the committed tree that spot falls inside the `time` box, but nothing is highlighted there.
- **An unknown agent:** a position naming an agent the tree doesn't have keeps him on the agents' level.

### 6 to 13: not started
Outside the scope of this run.

## Files changed

- `src/autobot/tui/tree_game.py` (new): `Position`, `Game` (the travels, cell, offset, highlight, level view) and `draw_protagonist`.
- `tests/tui/test_tui_tree_game.py` (new): tests of the travels, the offset, the highlight, the level view and the protagonist's drawing.

## For you to do

- Run `make test` and `make lint` outside the sandbox, so the socket tests can run and `ruff format --check .` can read `.claude/loop.md`.
- `test_the_committed_config_is_a_fresh_install` is still failing, as it has since before step 1.
- Mark step 5 as done in the plan's frontmatter; I'm not allowed to edit the plan.
- Step 10 should call `offset(now, rows, cols)`, `highlight(now)` and `level_view(now)` with the signatures above.
