# GPU inference server plan, steps 5 to 12: what was done

Written outside cr2, in Claude Code, by the model claude-fable-5-2, on 2026-11-27, on the NUC (`branch`), in a git worktree that started from the checkout's uncommitted work of steps 1 to 4.

## Outcome

Steps 5, 7, 8, 9, 10 and 11 are built and their "Done when" holds. Steps 6 and 12 are built and tested as far as the NUC allows, and are stamped `failed` in the plan because their "Done when" needs `GAMEON1`, which this session could not reach: nothing has been set up or run there.

- `make lint` and `make typecheck` pass. `pytest` passes 1,818 tests. Three fail, and failed the same way before this work: two because the owner's own settings in `config/autobot.toml` differ from a fresh install (theme `sun`, the camera's confirmation on), and one only in the worktree, whose `models` is a symlink.
- The real HTTP server and the NUC's real client were run against each other once on loopback, with fake models: `/health`, `/v1/stt` with the committed fixture, `/v1/route`, `/v1/chat` whole and streamed, a wrong token (`401`) and a stopped server (refused in 0.06 s). That is not a test in the suite, which opens no socket.
- The interface was opened once in a pseudo-terminal on scratch directories: the main menu shows `Server  down: no server token: run make server-token`, which is right, since the token is the owner's to make.

## By step

- **5.** `runtime.py` (the seam, and the engine of the `route` and `chat` jobs), `ollama.py`, `route.py`, `chat.py`. `Job.stream` holds a streamed answer's place until its last piece.
- **6.** `control.py`, `check.py`, `token.py`, `fetch_models.py`, `deploy/autobot-server.service`, the `server-*` targets, and on the NUC `autobot.server.token` and `autobot.server.ping`. `INFRASTRUCTURE.md` gained "Setting the server up on `GAMEON1`".
- **7.** `autobot.server.client`, `hints`, `watch`; `ServerHearing` and `Heard` in `autobot.agents.hearing`.
- **8.** `autobot.tui.server_state`; the `Server` line and the badge; the *notice* reaction and the ticker's line.
- **9.** `autobot.embedding`, `autobot.cascade`; the router decides through the cascade; `Position.tier`; example phrases on the Agent routing screen; the embedding model in `make fetch-models`.
- **10.** `Model.chat`, `ServerModel`, `autobot.agents.models`; `Seams.models`; the editor's Model and Fallback fields.
- **11.** `autobot.agents.core`; the core agent in the registry and on the tree; `Seams.tools`.
- **12.** `autobot_server.eval`, `server/eval/utterances.jsonl` (60 labelled, typed utterances).

## Where this differs from the plan, and why

- **`[server] stt_enabled`, shipped `false`.** A key the plan's step 1 does not list. `CONSTRAINTS.md` `CN-1` still says speech-to-text runs on the local machine, and the plan says server transcription is switched on only once the amendment lands. Without a switch there was nothing to leave off.
- **Gemini is passed over only where no Gemini model is set up.** The plan says "while no model is chosen". Skipping it whenever `[gcp] gemini_model` is empty changed what the `question` agent says today; the Gemini model already reports that itself when asked, which passes it over.
- **The core agent's next turn is the listener's transcript**, gathered until the owner has paused 1.2 s. It does not record a clip of its own as the `question` agent does, so `ServerHearing` would not hear the core agent's turns even with `stt_enabled` on.
- **Parameters and Tools are shown in the Agent editor and not edited there.** They are nested tables, which the editor's one-line fields and `config_edit.Change` cannot write.
- **No example phrases ship.** A test of step 1 asserts the shipped rules have none. Measured on the NUC, tier 2 against the bare commands scores 0.34 to 0.56, under the 0.6 threshold, so tier 2 will rarely fire until the owner adds examples on the Agent routing screen.
- **The embedding model was fetched** into `models/embedding/all-MiniLM-L6-v2/` with autobot's own fetcher, to confirm the two URLs.

## Not done

- Everything on `GAMEON1`: `make server-setup`, `server-fetch-models`, `server-token`, `server-install`, `server-check`, `server-eval`, and `make server-ping` from the NUC.
- The plan's "Changes needed elsewhere": the amendments to `CONSTRAINTS.md`, `CONVENTIONS.md`, `SCHEMA.md`, `ARCH.md`, `GLOSSARY.md`, `AGENTS.md`, ADR 0007 and the other specs. The plan puts them out of scope.
- Nothing was committed: `AGENTS.md` reserves commits for a person.
