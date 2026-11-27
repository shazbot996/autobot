# Finish the agents and the agent tree plan: what was done

Written outside cr2, in Claude Code, by the model claude-fable-5-2, on 2026-11-27.

Steps 6 to 13 of `docs/plans/active/agents-and-the-agent-tree-PLAN.md` are built and stamped `completed`; steps 1 to 5 were run in cr2 before. Four background agents built the weather lookup with the secret read and the zip-code table, the Gemini ADK seam, the Agent editor and the Word lists screen, each against an interface fixed beforehand; the session, the five agents, the face's game screen and the wiring were written in the main session.

## The checks

- `make lint` and `make typecheck` pass.
- `make test`: 1373 pass and 1 fails. The failure is `tests/test_config.py::test_the_committed_config_is_a_fresh_install`, which wants `[tui] theme = "light"`; the file's theme is the owner's setting and failed this test before the run.
- `node .agents/skills/cr2-conformance/scripts/check.mjs` passes, with its two warnings about section headings.

## What was verified beyond the tests

- The `time` and `introduce` agents were run once with the real local piper voice and a fake speaker: both rendered and ended.
- `make fetch-models` was run once: it fetched the Census zip-code table into `models/geo/zcta.tsv`, and `locate` resolved a zip code from it.
- The real application was opened in a pseudo-terminal: the main menu, the Agent editor's tree and an agent's view drew. The face was not opened there, because on the NUC it opens the microphone.

## What was not verified

- No question has been asked of Gemini: the ADK turn in `autobot.agents.gemini` has never run against Vertex AI.
- No weather has been looked up: `GoogleWeather` has only parsed responses shaped like Google's documentation.
- The camera agent has not started its commands on the desktop.

## What differs from the plan

- **Seams.** The agents reach the wall clock, the local commands, Google Cloud, the weather lookup and the model through `FaceContext.seams` (`autobot.tui.face.Seams`), filled from `Services`; the plan names only the `Services` fields.
- **The running view lingers.** An agent's view stays for twenty seconds after the agent ends, or until the next wake. The plan returns to the map when the agent is done, which would take an answer off the screen as soon as it was spoken.
- **The keyword hint is in the map panel's edge**, not beside the level: the map slides, and at 72 columns a keyword beside the level was drawn over a box.
- **The box is green from the command on**, not only once the protagonist arrives.
- **Today's weather is two calls** to the Weather API and is counted as two against the agent's limits; the days ahead are one.
- **The question agent refuses the Gemini Developer API**: only Vertex AI, as the service account, is built.
- **The main menu at the minimum height.** With fourteen entries its state panel has three rows there, so the training line is left out when the state's sentence takes two. Its key bar shows Tab and no longer shows Esc.
- **Extra test files**: `tests/agents/test_agent_weather_api.py` and `tests/agents/test_agent_gemini.py`, beside the ones the plan lists. `tests/tui/test_tui_routing_screen.py` changed for the menu.
- **The Agent editor's view** is titled `Agent editor: <name>`, and an edit of an agent that has no `[[agents]]` table in the file is refused in one line.

## Not done, as the plan says

The context documents, the other specs and a new ADR, which the spec's "Changes needed elsewhere" lists.
