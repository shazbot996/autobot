---
parent: docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md
type: plan
title: Agents and the agent tree
authority: prescriptive
status: active
describes:
  - pyproject.toml
  - config/autobot.toml
  - src/autobot/config.py
  - src/autobot/routing.py
  - src/autobot/fetch_models.py
  - src/autobot/tui/config_edit.py
  - src/autobot/agents/__init__.py
  - src/autobot/agents/base.py
  - src/autobot/agents/registry.py
  - src/autobot/agents/response.py
  - src/autobot/agents/speech.py
  - src/autobot/agents/words.py
  - src/autobot/agents/confirm.py
  - src/autobot/agents/tree.py
  - src/autobot/agents/time_of_day.py
  - src/autobot/agents/introduce.py
  - src/autobot/agents/camera.py
  - src/autobot/agents/weather.py
  - src/autobot/agents/weather_api.py
  - src/autobot/agents/question.py
  - src/autobot/agents/gemini.py
  - src/autobot/gcp/client.py
  - src/autobot/tui/app.py
  - src/autobot/tui/runner.py
  - src/autobot/tui/echo.py
  - src/autobot/tui/face.py
  - src/autobot/tui/router.py
  - src/autobot/tui/tree_game.py
  - src/autobot/tui/agent_edit.py
  - src/autobot/tui/screens/face.py
  - src/autobot/tui/screens/main.py
  - src/autobot/tui/screens/routing.py
  - src/autobot/tui/screens/agent_editor.py
  - src/autobot/tui/screens/agent_words.py
  - tests/test_config.py
  - tests/test_config_agents.py
  - tests/test_routing.py
  - tests/gcp/gcp_fakes.py
  - tests/gcp/test_gcp_client.py
  - tests/agents/__init__.py
  - tests/agents/agent_fakes.py
  - tests/agents/test_agent_registry.py
  - tests/agents/test_agent_response.py
  - tests/agents/test_agent_words.py
  - tests/agents/test_agent_confirm.py
  - tests/agents/test_agent_tree.py
  - tests/agents/test_agent_time.py
  - tests/agents/test_agent_introduce.py
  - tests/agents/test_agent_camera.py
  - tests/agents/test_agent_weather.py
  - tests/agents/test_agent_question.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_config_edit.py
  - tests/tui/test_tui_router.py
  - tests/tui/test_tui_tree_game.py
  - tests/tui/test_tui_face_screen.py
  - tests/tui/test_tui_app.py
  - tests/tui/test_tui_agent_edit.py
  - tests/tui/test_tui_agent_editor.py
  - tests/tui/test_tui_agent_words.py
  - AGENTS.md
  - README.md
  - CHANGELOG.md
provenance:
  - run: schiele/0113
    at: 2026-11-27T17:55:54.396Z
    mode: create
  - run: schiele/0114
    at: 2026-11-27T18:03:47.150Z
    mode: revise
  - run: schiele/0115
    at: 2026-11-27T18:06:46.030Z
    mode: revise
---

# Agents and the Agent Tree — Implementation Plan

Implements the autobot — Agents and the agent tree spec (`docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md`).

This is an initial plan. Nothing of the spec is built: there is no `src/autobot/agents/` package, no file named `agent*` or `tree*` under `src/autobot/tui/`, and no `[[agents]]` table in `config/autobot.toml` or in `autobot.config.parse_config`. What exists is the ground the spec stands on: the face and its `Experience` protocol (`tui/face.py`), the router that listens ten seconds and starts an agent's experience (`tui/router.py`), the echo (`tui/echo.py`), the `[[routes]]` rules with `command` and `agent` only and `ROUTE_AGENTS = ("echo",)` (`config.py`, `routing.py`), the Agent routing screen, the main menu with **Face** as its tenth item, the eyes as data (`face_reactions.py`), and the Google Cloud package with its service-account client, its `store_secret` path and the expected secret `autobot-weather-api-key`. `config_edit` writes one-line scalars only and refuses a multi-line string, which AG-10 needs for prompts. The order below builds the data first, then the models that do no drawing, then the session, then the agents from the simplest to the one that reasons, and the screens last.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| AG-1 An agent is a unit of code with a definition | The echo is a bare `Experience`; agents are names in `ROUTE_AGENTS`; no definition | `AgentConfig` loaded from `[[agents]]`; an `Agent` shape in `agents/base.py` (definition, `about` text, experience factory, flags) and a registry of the six agents, the echo included |
| AG-2 The tree: root, agent nodes, command edges, aliases, keywords, confirmation marks | Nothing | `agents/tree.py`: one model built from the routes, the agents and the word lists, laid out as text in the theme's glyphs or ASCII, with node and edge positions for both screens |
| AG-3 Game screen: map, protagonist, movement, running view as a level | The detail panel shows the experience's lines only | `tui/tree_game.py`: the protagonist's place on the map and the map's offset over time, following the session's state; `screens/face.py` draws the map centred on him, or the agent's running view while one runs |
| AG-4 Two-part response: TTS summary and detail visual | `FaceContext.say` speaks one text; no detail shaping | `agents/response.py`: `Response(summary, detail)`, laid out for the panel's size; `agents/speech.py`: the shared speak-then-hold steps the echo does by hand |
| AG-5 `question` agent | Nothing | `agents/question.py` over a `Model` seam in `agents/gemini.py` (Gemini ADK on Vertex AI as the service account), asked on a thread, answering in two parts |
| AG-6 `weather`, `time`, `camera`, `introduce` | Nothing | Four agents under `agents/`; the weather lookup behind a seam with its key from Secret Manager and its own rate control; the camera's two commands started detached in `assistant/`, NUC only |
| AG-7 Mode on a rule; aliases | `RouteConfig(command, agent)` | `mode` on `RouteConfig`, passed to the agent; two rules to one agent drawn as two edges; the Agent routing screen lists them as two rules |
| AG-8 Tab between run and configuration mode; Agent editor replaces Face in the menu; opens on the menu | `make launch` opens on the main menu (`App.run`); Face is menu item ten; no Tab | Tab in `App.dispatch` opens the face from the main menu and leaves it; Esc leaves it too; **Agent editor** takes item ten; key bars show Tab |
| AG-9 Agent editor screen walks the tree | Nothing | `screens/agent_editor.py`: the same `tree.py` picture with a selection moved by the arrow keys; Enter opens the agent's view, or says the root is the wake phrase |
| AG-10 Agent editor view edits the definition, not the code | Nothing; `config_edit` refuses multi-line strings | `tui/agent_edit.py` (fields, validation, `Change`s, the text area model) and the view in `screens/agent_editor.py`; `config_edit` learns multi-line basic strings and one-line arrays; the API key goes through `store_secret` |
| AG-11 State stays on this machine, in this layer | No agent keeps state | No step: no agent of this spec keeps state. `agents/base.py` documents where it goes (beside the definition, on the NUC) |
| AG-12 Local speech, text-only network, testable, secrets out of the file | Speech is local; `gcp.client` is the one way to Google Cloud; nothing reads a secret's value | Every new model in a module that does no drawing, tested with fakes for the model, the lookup, the secret and the launch; `GoogleCloud.secret_value` added; a missing credential is one *trouble* line |
| AG-13 Two confirmations per agent, shared yes and no lists | Nothing | `confirm_entry`, `confirm_request` and their questions on `AgentConfig`; `[words] yes` and `[words] no` with Midwest defaults; `agents/confirm.py` and the router's confirmation phase |
| AG-14 `cancel` global action keyword, one level back | Nothing | `[words] actions = ["cancel"]`; `agents/words.py` matches before the rules; the router steps back one level |
| AG-15 Listening setting per agent | Nothing | `listening` on `AgentConfig`; while off, the router does not read transcripts during the running view |

## 1. Configuration: agent definitions, modes, word lists, and writing them back

**Files:** modified `src/autobot/config.py`, `src/autobot/routing.py`, `src/autobot/tui/config_edit.py`, `config/autobot.toml`, `tests/test_config.py`, `tests/test_routing.py`, `tests/tui/test_tui_config_edit.py`; new `tests/test_config_agents.py`.

- `config.py`:
  - `ROUTE_AGENTS` becomes `("echo", "question", "weather", "time", "camera", "introduce")`, with a constant per name.
  - `RouteConfig` gains `mode: str = ""`. `_routes` accepts the key, strips it, and still refuses a command configured twice.
  - `AgentConfig`, one per `[[agents]]` table: `name` (one of `ROUTE_AGENTS`, each at most once), `description`, `prompt` (default empty), `confirm_entry: bool = False`, `confirm_entry_question`, `confirm_request: bool = False`, `confirm_request_question`, `listening: bool = True`, and the agents' own settings as optional keys: `zip_code`, `daily_limit: int = 100`, `minute_limit: int = 10`, `secret: str = "autobot-weather-api-key"` (weather); `folder: str = "assistant"`, `commands` (camera; a multi-line string, one command per line). A key an agent does not use is refused by name. Validation is one line: a zip code is five digits; a limit is a positive whole number.
  - `DEFAULT_AGENTS`: one definition per name, with the defaults the file below seeds, used for any agent the file does not define, so a file with no `[[agents]]` still loads, as `DEFAULT_ROUTES` does today. `Config.agent(name)` looks one up.
  - `WordsConfig` from the optional `[words]` table: `yes`, `no`, `actions`, lists of non-empty strings, each word in one list only. Defaults: yes `["yes", "yeah", "yep", "yup", "sure", "okay", "ok", "affirmative", "go ahead", "you bet", "you betcha", "do it", "absolutely", "please do"]`; no `["no", "nope", "nah", "no way", "negative", "stop", "don't", "never mind"]`; actions `["cancel"]`.
  - `parse_config` admits `agents` and `words` at the top level; `DEFAULT_ROUTES` becomes the six default rules in the tree's order: `question`, `weather`, `forecast` (weather, mode `forecast`), `time`, `camera`, `cameras` (camera), `introduce`, `echo`.
- `routing.py`: `AGENT_NOTES` gains the five agents; `Match.describe` names the mode when the rule has one (`rule 3: "forecast" -> weather agent (forecast)`).
- `config_edit.py`: `Scalar` widens to `str | int | float | bool | list[str]`. `format_value` writes a list as a one-line TOML array of basic strings, and a string containing a newline as a multi-line basic string (`"""` on its own lines, with `\` and `"""` escaped). `set_value` recognises three value forms on a key's line: the one-line scalar it has now, a one-line array `[...]` with no `]` inside a string, and a multi-line basic string spanning from an opening `"""` to the line that closes it, which it replaces as a block. Verification is unchanged: the parsed result must equal the old data with exactly the change applied, then pass `parse_config`.
- `config/autobot.toml` gains, with comments: `mode` on the `forecast` rule, the six default rules, a `[words]` table with the defaults above, and one `[[agents]]` table per agent with its description, the question agent's prompt (asking for a spoken summary and a detail visual in the shape step 2 parses), the introduce agent's script, the weather agent's `zip_code = ""`, limits and secret, the camera agent's `folder` and two commands, both confirmations off with a default question each (`"This may cost a few dollars. Are you sure you want to run this?"` for the request), and `listening = true`.

**Done when:** `load_config()` on the committed file gives six agents, a `forecast` rule whose `mode` is `forecast`, and `words.actions == ("cancel",)`. `tests/test_config_agents.py` shows that an unknown agent name, a duplicate agent, a `zip_code` on the camera agent, a word in two lists, and a bad limit each give a one-line `ConfigError`, and that a file without `[[agents]]` or `[words]` loads the defaults. `tests/tui/test_tui_config_edit.py` shows a prompt with three lines written as a `"""` string and read back unchanged with every comment intact, a list written as `["yes", "yeah"]`, a multi-line value replaced by a one-line one and back, and a bool toggled in `[[agents]]` entry 3. `make test`, `make lint`, `make typecheck` pass.

## 2. The agent shape, the registry and the two-part response

**Files:** new `src/autobot/agents/__init__.py`, `src/autobot/agents/base.py`, `src/autobot/agents/registry.py`, `src/autobot/agents/response.py`, `src/autobot/agents/speech.py`, `tests/agents/__init__.py`, `tests/agents/agent_fakes.py`, `tests/agents/test_agent_registry.py`, `tests/agents/test_agent_response.py`; modified `src/autobot/tui/echo.py`, `src/autobot/tui/router.py`, `tests/tui/test_tui_router.py`.

- `base.py` defines the one shape every agent is an instance of (AG-1):
  - `Agent` protocol: `name`; `about` (the pre-programmed text saying what the code does, AG-10); `uses_model: bool`; `sends_requests: bool` (whether the before-a-request confirmation is offered, AG-13); `settings` (the names of the definition keys it owns, for the editor); `make(definition, mode) -> Experience` (the experience it runs on the face, FS-10).
  - `Launch` dataclass: what the router hands an agent when it starts: the `AgentConfig`, the `mode`, and the transcript it matched.
  - A docstring paragraph, not code, that any state an agent later keeps lives on the NUC beside its definition and is owned here, not by an ADK agent (AG-11).
- `response.py`: `Response(summary: str, detail: tuple[str, ...])`. `layout(response, rows, cols) -> list[str]` fits the detail to the panel: each line cut or folded to `cols`, at most `rows` lines, the first ones kept, nothing scrolled into (AG-4). `parse_model_response(text) -> Response` takes the shape the question prompt asks for, a JSON object `{"summary": ..., "detail": [...]}`, from the model's text (a fenced block is unwrapped); any text that is not that shape becomes a `Response` whose summary is its first sentence and whose detail is the whole text folded.
- `speech.py`: `Speaker`, the steps the echo does by hand in `_speaking`: `prepare` the line, wait for the render, hold the listener for the clip's length, play it with the eyes *speaking* and the mouth following, `done` when playback ends; a render error is one line. The echo's `_speaking` is rewritten on it, with the same tests passing.
- `registry.py`: `AGENTS: Mapping[str, Agent]` by name, filled in steps 7 to 9; here it holds the echo, wrapped as an `Agent` whose `about` is today's `AGENT_NOTES` text, `uses_model=False`, `sends_requests=False`. `router.AGENTS` is replaced by the registry, and `Router._route` calls `agent.make(Launch(config.agent(name), route.mode, transcript))`.
- `agent_fakes.py`: a `FakeModel` that answers a scripted text, a `FakeLookup` for the weather, a `FakeLaunch` that records argv and cwd, a `FakeSecrets` that answers a value, used by every agent test and by `tui_fakes.py`.

**Done when:** `test_agent_registry.py` shows every name in `ROUTE_AGENTS` is in the registry once step 9 lands (until then, it checks the echo and the names built so far) and that `make` gives an `Experience`. `test_agent_response.py` shows a JSON answer parsed into its two parts, a plain-text answer folded, and a twelve-line detail laid out in a 5×40 panel as five lines of at most forty characters. `tests/tui/test_tui_echo.py` passes unchanged with the echo speaking through `Speaker`.

## 3. Words: global action keywords and confirmation answers

**Files:** new `src/autobot/agents/words.py`, `src/autobot/agents/confirm.py`, `tests/agents/test_agent_words.py`, `tests/agents/test_agent_confirm.py`.

- `words.py`: `match_word(transcript, words) -> str | None`, the first word of a list that stands in the transcript by the containment rule of `routing.contains_command`, so "please cancel that" matches `cancel`. `action_keyword(transcript, config)` tries `[words] actions` (AG-14).
- `confirm.py`:
  - `Answer` enum: `YES`, `NO`, `NOTHING`. `answer(transcript, words) -> Answer`: `NO` when a no word or an action keyword stands in it, else `YES` when a yes word does, else `NOTHING` (AG-13: a word that is neither counts for nothing).
  - `Confirmation` state machine, driven by `tick(face)` and never blocking: it speaks the question through `Speaker` (AG-12), then reads `face.heard` until a yes or a no, or until `LISTEN_SECONDS` (the router's ten seconds) pass after the question was spoken; its `outcome` is `yes`, `no` or `timed out`. It plays *listening* while it waits.

**Done when:** `test_agent_words.py` shows "cancel", "Cancel!" and "could you cancel" match and "cancellation" does not. `test_agent_confirm.py` shows "you betcha" is `YES`, "nah" is `NO`, "cancel" is `NO`, "maybe later" is `NOTHING`, and that a `Confirmation` driven with the fake voice and a fake clock speaks its question once, holds the listener for the clip's length, ends `yes` on "sure", `no` on "nope", and `timed out` after ten silent seconds.

## 4. The agent tree

**Files:** new `src/autobot/agents/tree.py`, `tests/agents/test_agent_tree.py`.

- `build_tree(config) -> Tree`: the root `Node("autobot")` from the first wake phrase, one `Node` per agent in the order of its first rule in `[[routes]]`, one `Edge(command, mode, node)` per rule, so two rules to one agent are two edges into one node (AG-2, AG-7). Each node carries `confirm_entry` and `confirm_request` marks from its definition (AG-13) and `uses_model` from the registry. `Tree.keywords` is `[words] actions`, shown beside both levels (AG-14). `Tree.levels` is two rows: `[root]` and the agents.
- `layout(tree, glyphs) -> Picture`: the text of the flowchart, top-down. Boxes in `glyphs.panel_*` corners and `rule`/`panel_vertical` edges, or `+`, `-`, `|` in ASCII; a line from the root's bottom to each agent box, each edge labelled with its command, and `(forecast)` after a command that carries a mode; a `?` mark in the box's top edge for a confirmation on the way in and `$?` for one before a request; the keywords as `[cancel]` at the right of each level. `Picture` holds `rows: list[str]`, `boxes: dict[node, Rect]`, `labels: dict[edge, (row, col)]` and `keywords: dict[level, (row, col)]`, so a screen can colour the node the protagonist stands on or the one selected without parsing text. A terminal narrower than the agents' level wraps nothing: the picture is as wide as it needs to be, and the game screen scrolls it under the protagonist (AG-3) while the editor scrolls it with the selection.
- `Picture.centre(node)` gives the cell at a node's centre, and `Picture.path(a, b)` the cells of the edge between the root and an agent, for step 5.

**Done when:** `test_agent_tree.py` builds the tree from the committed config and shows: six agent nodes in rule order, `weather` with two edges labelled `weather` and `forecast (forecast)`, `camera` with two edges, `[cancel]` beside both levels, a `?` mark appearing on `question` when `confirm_entry` is set, every ASCII row printable ASCII, every Unicode row the same width, and `boxes` covering the text of each name.

## 5. The game screen's movement

**Files:** new `src/autobot/tui/tree_game.py`, `tests/tui/test_tui_tree_game.py`.

- `Position` dataclass: where the session stands: `level` (`root`, `agents`), `node` (an agent's name or `""` while listening for a command), `phase` (`listening`, `wake`, `command`, `confirming`, `running`, `done`). The router of step 6 publishes one each frame.
- `Game`: `follow(position, now)` and `offset(now)`. It keeps the protagonist's map cell as a travel from its cell to the target's cell along `Picture.path`, started when the position changes, lasting `TRAVEL_SECONDS = 0.4` (AG-3: well under a second), interpolated by the clock, and the map's offset is whatever puts that cell at the panel's centre. Nothing in it waits or sleeps, and nothing in the router waits on it: it reads the position the session already reached (AG-3, AG-12). `Game.highlight` is the node under the protagonist once the travel ends. `Game.level_view` is `False` until the phase is `running`, when the face draws the agent's running view instead of the map.
- `draw_protagonist(pose, glyphs) -> list[str]`: the mini-autobot, two rows: a compact pair of the face's eyes, `(o)(o)` with the pupil following `pose.across` in ASCII and `╭─╮` strokes in Unicode, so the eyes are the ones `face_reactions` animates (AG-3).

**Done when:** `test_tui_tree_game.py`, with the committed config's picture and a fake clock, shows: at `listening` the offset centres the root; after `wake` the protagonist is between the levels at 0.2 s and on the agents' level at 0.4 s; after `command weather` it reaches the weather box and `highlight == "weather"`; `running` sets `level_view`; `done` returns it to the root; a position change mid-travel starts the new travel from the current cell, and no frame moves it more than one travel's distance.

## 6. The session: levels, keywords, confirmations, listening

**Files:** modified `src/autobot/tui/router.py`, `src/autobot/tui/face.py`, `tests/tui/test_tui_router.py`.

- `Router` gains a `position: Position` it updates each frame, and these phases after the wake (AG-3, AG-13, AG-14, AG-15):
  1. **Agents' level.** As today: the summary lists the commands, the eyes play *listening*, and each transcript is tried for `LISTEN_SECONDS`. Before the rules, `action_keyword` is tried: `cancel` here ends the session (one level up is the root) with the summary line `Cancelled.` and *done*.
  2. **Command.** A match sets `position.node`, names the rule and mode in the detail, and reads the agent's definition. With `confirm_entry` on, a `Confirmation` of step 3 runs at the node (`phase = confirming`); `yes` goes on, `no` or `timed out` returns to the agents' level with the ten seconds started again. Otherwise the experience starts at once, as the echo does now.
  3. **Running.** `phase = running`; frames go to the agent's experience. While the agent's `listening` is on, each transcript is first tried for an action keyword: `cancel` ends the experience (`Experience.cancel()` is a new optional method; an experience without one is dropped and the face's playback stopped) and returns to the agents' level, listening again. While `listening` is off, `face.heard` is cleared each frame and nothing is read. A `Confirmation` an agent asks before a request (`FaceContext.confirm(question)`, see below) listens even then.
  4. **Done.** The experience ends: `position` returns to the root and the session ends.
- `FaceContext` gains `confirm(question) -> Confirmation`, which an agent with `confirm_request` on runs before its request goes out (AG-13), and `respond(response)`, which speaks the summary through `Speaker`, shows it in the summary panel and lays the detail into the panel at its last-shown size (AG-4). `DetailPanel` records its last `rows` and `cols`.
- `FaceModel` exposes `position` (the router's, or `listening` at the root when idle).

**Done when:** `test_tui_router.py` shows, on the fake rig: "cancel" at the agents' level ends the session; "cancel" during a fake agent calls its `cancel`, returns to the agents' level with `LISTENING` in the summary, and a command then still routes; a fake agent with `listening = false` does not hear "cancel"; an agent with `confirm_entry` on speaks its question and starts only on "yes", returning to the agents' level on "nope" and after ten seconds; `position` goes `root → agents → weather → running → root` across one weather-mode session; the mode `forecast` reaches the agent's `make`.

## 7. The local agents: `time`, `introduce`, `camera`

**Files:** new `src/autobot/agents/time_of_day.py`, `src/autobot/agents/introduce.py`, `src/autobot/agents/camera.py`, `tests/agents/test_agent_time.py`, `tests/agents/test_agent_introduce.py`, `tests/agents/test_agent_camera.py`; modified `src/autobot/agents/registry.py`, `src/autobot/tui/app.py`, `src/autobot/tui/runner.py`, `tests/tui/tui_fakes.py`.

- `time_of_day.py`: reads the NUC's clock through a `now` callable (the services' wall clock; tests give a fixed one) and responds `It's 3:42 in the afternoon.` with a detail of the date, the time to the second and the time zone (AG-6). No network.
- `introduce.py`: its `Response` is the definition's `prompt`, the written script, spoken as it stands, with the detail being the script's lines folded (AG-6, AG-10). `uses_model=False`.
- `camera.py`: refuses off the NUC with `face.refusal("the camera agent")` (TS-11). On it, it splits `commands` into lines and starts each in `repo / folder` through `Services.launch`, detached, in order, without waiting for either to end (AG-6); a folder that does not exist or a launch that fails is one *trouble* line. Its response says the cameras are opening in Firefox, and its detail lists the two commands and where they ran. It stops nothing and closes nothing.
- `runner.py`: `launch_detached(argv, cwd) -> str` starts a process with `subprocess.Popen` whose output goes to `run/agents.log`, started in its own session so it outlives the face, and returns the one-line error or `""`. `Services.launch` defaults to it; `tui_fakes` gives `FakeLaunch`. `Services.wall_clock` defaults to `datetime.now`.
- `registry.py` registers the three.

**Done when:** `test_agent_time.py` shows a fixed clock of 15:42 gives `It's 3:42 in the afternoon.`. `test_agent_introduce.py` shows the summary equals the script. `test_agent_camera.py` shows that on `hostname="nuc"` the fake launch records `["make", "fourway-server"]` then `["make", "fourway"]`, both with `cwd == repo / "assistant"`, that no task is waited on, that off the NUC the summary holds `the camera agent runs on the NUC only; this host is testbox` under *trouble*, and that a missing folder is one line.

## 8. The `weather` agent: the lookup, the key and the rate

**Files:** new `src/autobot/agents/weather.py`, `src/autobot/agents/weather_api.py`, `tests/agents/test_agent_weather.py`; modified `src/autobot/gcp/client.py`, `src/autobot/fetch_models.py`, `tests/gcp/gcp_fakes.py`, `tests/gcp/test_gcp_client.py`, `src/autobot/agents/registry.py`, `src/autobot/tui/app.py`, `tests/tui/tui_fakes.py`.

- `gcp/client.py`: `GoogleCloud.secret_value(secret) -> bytearray` reads the latest enabled version through `secrets/<name>/versions/latest:access`, decodes the payload into a `bytearray` the caller clears; `FakeGoogleCloud` answers from its stored versions. Reading needs `secretmanager.versions.access`, which the catalogue's `roles/secretmanager.secretAccessor` already grants.
- `fetch_models.py`: `make fetch-models` also fetches the Census ZCTA gazetteer (about 1 MB, US zip-code centroids) into `models/geo/`, the one target that downloads.
- `weather_api.py`:
  - `Lookup` protocol: `today(zip_code, key) -> Forecast` and `days(zip_code, key) -> Forecast`, where `Forecast` is plain data (place, condition, temperatures, precipitation, a line per day). `GoogleWeather` implements it over `requests` against `https://weather.googleapis.com/v1/currentConditions:lookup` and `forecast/days:lookup`, with the key as a query parameter, refusing any other host, and turning every failure into a one-line `WeatherError` that never contains the key. Google's Weather API takes a latitude and longitude, so the zip code is resolved by `locate(zip_code)`, which reads the gazetteer under `models/geo/` locally; no second API is called and the key stays restricted to the one API. A missing table is one line naming `make fetch-models`, and a zip code not in the table is one line naming the zip code.
  - `RateControl(daily_limit, minute_limit, path)`: keeps the moments of the calls in `run/weather-calls.json`, refuses a call past either limit with one line saying which limit and when it clears (AG-6: the agent's control is the first line; GC-10's cap is the backstop), and records a call only when it goes out.
- `weather.py`: `uses_model=False`, `sends_requests=True`. Its experience: refuse when `zip_code` is empty (`the weather agent has no zip code: set it in the Agent editor`); connect as the service account through `Services.gcp_connect` and read `secret` with `secret_value` (a missing key, a refused identity or an empty secret is one *trouble* line, AG-12); run the before-a-request `Confirmation` when `confirm_request` is on; check the rate; then do the lookup on `Services.spawn` with the eyes *working*, and respond: mode `""` is today's conditions as a short spoken narrative with a detail visual of the day; mode `forecast` is the days ahead, one line a day (AG-7). The key is cleared after the call.
- `Services.weather_lookup: Lookup` defaults to `GoogleWeather()`; tests give `FakeLookup`.

**Done when:** `test_gcp_client.py` shows `secret_value` on the fake returns the stored bytes and a 404 is one line. `test_agent_weather.py` shows: with a `FakeLookup` and a fake cloud holding the key, mode `""` calls `today` once and the summary is one sentence naming the condition and temperature; mode `forecast` calls `days`; an empty zip code, no key file, and an empty secret each end in one *trouble* line without a lookup; the eleventh call in a minute is refused by `RateControl` with a line naming the minute limit and nothing is looked up; the key's bytes are zero after the call; `confirm_request` on asks first and "no" skips the lookup; `locate` against a small gazetteer written to a temporary directory returns the latitude and longitude of a known zip code, a zip code not in it is one line, and a missing table is one line naming `make fetch-models`.

## 9. The `question` agent and the Gemini ADK seam

**Files:** new `src/autobot/agents/question.py`, `src/autobot/agents/gemini.py`, `tests/agents/test_agent_question.py`; modified `pyproject.toml`, `src/autobot/agents/registry.py`, `src/autobot/tui/app.py`, `tests/tui/tui_fakes.py`.

- `pyproject.toml` adds `google-adk>=1.0` to the runtime dependencies with a comment that it carries text only (ADR 0002, `CN-1`), and a mypy override for `google.adk.*`. No speech library is added.
- `gemini.py`: `Model` protocol: `ask(prompt, query) -> str`. `AdkModel(config, repo)` implements it: it connects through `gcp.client.connect` first, so the identity is the service account or the call is refused in one line (GC-4); sets `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` from `[gcp]`; builds an ADK `LlmAgent` on `[gcp] gemini_model` with the prompt as its instruction and runs it with an `InMemorySessionService` for one turn (no state is kept, AG-11). `google.adk` is imported inside `ask`, so importing the module loads nothing. Every failure is a one-line `ModelError`; an empty `gemini_model` is `no Gemini model is chosen: pick one on the Google Cloud screen`.
- `question.py`: `uses_model=True`, `sends_requests=True`. The query is the matched transcript with the command word and what precedes it removed (`"I have a question, how far is the moon"` → `how far is the moon`); an empty query says so and ends. It runs the before-a-request `Confirmation` when on, then asks on `Services.spawn` with the eyes *working* and the summary `Thinking...`, parses the answer with `parse_model_response`, and responds in two parts (AG-4, AG-5). The text sent is the listener's local transcript and nothing else.
- `Services.model: Callable[[Config, Path], Model]` defaults to `AdkModel`; tests give `FakeModel`.

**Done when:** `test_agent_question.py` shows: the fake model receives the prompt from the definition and `how far is the moon`; a JSON answer gives the summary spoken and the detail laid out; a plain answer still gives both parts; a `ModelError` is one *trouble* line; the fake model is not called when the entry confirmation is answered "no"; and `python -c "import autobot.agents.gemini"` on a checkout imports neither `google.adk` nor any speech library (asserted on `sys.modules`). The registry test of step 2 now sees all six agents.

## 10. The face: the game screen, the running view, Tab and the menu

**Files:** modified `src/autobot/tui/screens/face.py`, `src/autobot/tui/app.py`, `src/autobot/tui/screens/main.py`, `src/autobot/tui/screens/routing.py`, `tests/tui/test_tui_face_screen.py`, `tests/tui/test_tui_app.py`.

- `screens/face.py`: `enter` builds the tree's `Picture` in the canvas's glyphs and a `Game`; `tick` feeds the game the model's `position`. `draw_detail` draws the map while `game.level_view` is off: the picture's rows offset so the protagonist's cell is the panel's centre, the box under him in `theme.ok` (the theme's green, AG-3), the keywords beside the level, the protagonist drawn over the map with the eyes' current pose; while a confirmation runs, its question is drawn under the node. With `level_view` on, the panel is the agent's running view: the detail lines as today, with the panel title the agent's name (AG-3). The panel title reads `Map` otherwise. Up, Down and the scroll keys keep scrolling the running view; they do nothing on the map.
- `app.py`: `KEY_TAB = 9`. `dispatch` handles it before `view.key` when `not self.typing()`: on the main menu it pushes `FaceScreen`; on the face it pops; elsewhere it does nothing. `FaceScreen.key` returns `False` for Tab so the app sees it; Esc already pops. The `HELP_LINES` gain `Tab            switch between the face and the main menu`.
- `screens/main.py`: the **Face** item goes; **Agent editor** takes its place as the tenth entry, hint `The agents on their tree: walk it with the arrows, Enter edits one`; **Word lists** follows **Agent routing** (step 12); `key_bar` gains `("Tab", "Face")`. `FaceScreen.key_bar` shows `("Tab", "Menu")` and keeps `Esc Back`. `TAGLINE` stays.
- `screens/routing.py`: a rule's label and the facts show its mode (`Rule 3: "forecast" -> weather agent (forecast)`); the agents fact lists the six names.

**Done when:** `test_tui_app.py` shows Tab from the main menu opens the face (frame title `Face`), Tab on the face returns to the menu, Tab while `prompt` has the keyboard types nothing and switches nothing, and the main menu's tenth item reads `Agent editor` with no `Face` item anywhere. `test_tui_face_screen.py` shows, at 72×22 and 120×40, that before a wake the detail panel shows the `autobot` box with the protagonist on it, that after fake `woke("hey autobot")` and frames the protagonist stands on the agents' level with `[cancel]` beside it, that after `transcript("weather")` the `weather` box's cells carry `theme.ok`, that during a fake agent the panel title is the agent's name and no box is drawn, and that `make launch` still opens on the main menu. On the NUC with `make up` running: Tab, "autobot", "time" moves the protagonist down and over, enters the time agent's view, speaks the time and returns him to the root.

## 11. The Agent editor: the tree screen and the editor view

**Files:** new `src/autobot/tui/agent_edit.py`, `src/autobot/tui/screens/agent_editor.py`, `tests/tui/test_tui_agent_edit.py`, `tests/tui/test_tui_agent_editor.py`.

- `agent_edit.py` (no drawing):
  - `Selection` over `Tree.levels`: `up`, `down` change the level, keeping the column nearest the current one; `left`, `right` move along the level; `current` is a node (AG-9).
  - `fields(agent, definition) -> list[Field]`: the view's rows in order: **Name** (read-only, `description` is the editable text), **Description**, **Commands** (read-only, `edited on the Agent routing screen`), **Language model** (read-only `yes`/`no`), **Prompt** (multi-line, only when `uses_model` or the agent is `introduce`, labelled **Script** there), **Confirm on the way in** (toggle) and its **Question**, **Confirm before a request** (toggle, only when `sends_requests`) and its **Question**, **Listening** (toggle), then the agent's own settings: `zip_code`, `daily_limit`, `minute_limit`, **API key** (secret, `stored in Secret Manager; never shown`) for `weather`; `folder` and `commands` (multi-line) for `camera`. Each field knows its `Change` (table `agents`, the definition's index) and its one-line complaint for a bad value.
  - `TextArea`: the multi-line field's model: lines, cursor, insert, backspace, newline, arrows, and `text()`; `typing` is true while it is open (TS-13).
- `screens/agent_editor.py`:
  - `AgentTreeScreen`, titled `Agent editor`: draws the step 4 picture with the selection's box in `theme.selected`, scrolled so the selection is visible; Up, Down, Left, Right move it; Enter on an agent pushes `AgentView`; Enter on the root says `The root is the wake phrase: the Wake phrase screen edits it` in the message bar (AG-9). F1 lists the keys.
  - `AgentView`, titled by the agent's name, full screen: the `about` text folded at the top, then the fields; Up and Down move between fields; Enter on a text field opens `app.prompt` with the field's complaint and saves through `app.save(change)` at once; Enter on a toggle flips and saves; Enter on the multi-line field opens the `TextArea` in the panel, Esc closes it and saves when the text changed; Enter on **API key** opens `app.prompt_secret` and stores the value with `gcp.secrets.store_secret` through `Services.gcp_connect` (AG-6, AG-10), so the value touches no file; the result is one line either way. `T` types a letter while a field has the keyboard.

**Done when:** `test_tui_agent_edit.py` shows the selection moving `root → question → weather → ... → root`, the field list for `weather` ending in `zip_code`, `daily_limit`, `minute_limit`, `API key`, no **Prompt** for `time`, a **Script** for `introduce`, and `TextArea` editing three lines. `test_tui_agent_editor.py` drives the real screens through the scripted fake screen: `0` from the main menu shows the tree with `autobot` selected; Down, Right, Enter opens `weather`; typing `43215` into `zip_code` writes `zip_code = "43215"` into the third `[[agents]]` table of the temporary config with every comment intact; toggling **Confirm on the way in** writes `confirm_entry = true`; editing the question agent's prompt writes a `"""` string; pasting a key records one `add_secret_version` on `FakeGoogleCloud` and the value is on no frame and in no file under `tmp_path`; Enter on the root shows the wake-phrase line; `T` inside the text area types `T`.

## 12. The Word lists screen

**Files:** new `src/autobot/tui/screens/agent_words.py`, `tests/tui/test_tui_agent_words.py`; modified `src/autobot/tui/screens/main.py`.

- `WordListsScreen(MenuView)`, titled `Word lists`, reached as **Word lists** after **Agent routing**: facts say what each list is for; one menu entry per list (**Yes words**, **No words**, **Global action keywords**) showing its words; Enter opens `app.prompt` with the words comma-separated, and the typed list, split on commas, trimmed, lower-cased and with duplicates and words of another list refused in one line, is saved as `Change("words", key, [...])` (AG-13, AG-14). A **Try a word...** entry says which list a typed sentence hits, using step 3's matching.

**Done when:** `test_tui_agent_words.py` shows the three entries with the defaults, that typing `yes, yeah, roger` writes `yes = ["yes", "yeah", "roger"]` to the temporary config, that `cancel` typed into the yes list is refused with one line naming the action keywords, and that `Try a word...` with "you betcha" says `yes`.

## 13. Agent guide, readme and changelog

**Files:** modified `AGENTS.md`, `README.md`, `CHANGELOG.md`.

- `AGENTS.md`: the interface's screens gain **Agent editor** and **Word lists** and lose **Face** from the menu; Tab opens the face and Tab or Esc leaves it; the agents are `echo`, `question`, `weather` (`forecast`), `time`, `camera` (`cameras`) and `introduce`; the camera agent and `run/agents.log` are named; the weather key is read only through `gcp.client`; `make fetch-models` also fetches the zip-code table under `models/geo/`.
- `README.md` "Running it": Tab opens the face; the first commands to try are "time", "introduce", "weather" once a zip code and key are set, and "I have a question ...".
- `CHANGELOG.md` Unreleased: the agent tree, the game screen (map, protagonist, the level), the Agent editor, the Word lists screen, the two modes and Tab, the mode on a rule, the five agents, the confirmations, `cancel`, the listening setting, the offline zip-code table, and the multi-line and array writes of the config editor.

**Done when:** the three files carry those lines, `node .agents/skills/cr2-conformance/scripts/check.mjs` passes when run outside cr2, and `make test`, `make lint` and `make typecheck` pass.

## 14. Choices the spec leaves open

1. **Agent-owned settings are keys of the one `[[agents]]` table**, refused by name on an agent that does not use them, rather than a sub-table per agent. One table keeps `config_edit`'s `Change(table, key, value, index)` as it is.
2. **The camera's commands are a multi-line string, one command per line**, not an array, so the same `TextArea` edits them and the prompt.
3. **The weather rate control keeps its call moments in `run/weather-calls.json`.** `run/` is runtime state of this machine (`CONVENTIONS.md` "Layout"), not agent memory, so AG-11 is not touched; a count held in memory would reset whenever the face reopened and leave only Google's cap.
4. **The protagonist's travel takes 0.4 s** and is interpolated from the clock each frame, so it never gates the session and a slow frame only shortens the animation.
5. **The running view keeps the face's frame.** The detail panel becomes the agent's, titled with its name; the summary, the eyes and the ticker stay, since FS-2 fixes the layout and AG-3 names the detail panel as the level.
6. **The model's answer shape is one JSON object** `{"summary": ..., "detail": [...]}`, asked for in the prompt and parsed leniently: any other text still becomes two parts.
7. **`question` strips the command word and what precedes it** to form the query, so "I have a question, how far is the moon" sends only the question.
8. **The before-a-request confirmation is run by the agent** through `FaceContext.confirm`, since only the agent knows when its request is about to go; the on-the-way-in one is run by the router, which owns that moment.
9. **Detached processes are started through one `Services.launch`** writing to `run/agents.log`, so the camera agent and any later agent that runs a local command share one tested path.
10. **The ADK runs with an in-memory session for one turn.** No state is kept yet (AG-11); the first agent that keeps some chooses the ADK session service pointed at the NUC.
11. **The zip code becomes coordinates through an offline table**, the Census ZCTA gazetteer fetched into `models/geo/` by `make fetch-models` and read locally by `weather_api.locate`, rather than Google's Geocoding API. It adds no API, no key widening and no call per lookup.

## 15. Out of scope

- Any agent keeping state or memory (AG-11 names where it goes; nothing goes there yet).
- Validation of a query by the rules layer before the `question` agent (RR-1), and rules that answer with anything but these five agents.
- A third level of the tree, nested agents, or a global action keyword other than `cancel`.
- Sound effects, colour beyond the theme's roles, or a protagonist with more than the eyes.
- Transcribing speech after a wake under a trained model (the face spec's open question): commands still need a `text_match` phrase.
- Running any `gcloud` command, enabling the Weather API, creating the key or the secret: the Google Cloud screen shows those, and the owner runs them.
- The context documents, the other specs, a new ADR: the spec's own "Changes needed elsewhere" lists them.

## Settled without asking

- **Derived (AS-1, ADR 0002).** The `question` agent reasons through Gemini ADK on Vertex AI as the service account, not through a direct REST call; `google-adk` is added as the one new dependency and imported inside the function that needs it.
- **Derived (GC-4, GC-9).** The weather key is read and written only through `gcp.client.connect`'s service-account connection: read with the new `secret_value`, stored with the existing `store_secret`.
- **Derived (GC-10).** The weather agent's default limits are 100 a day and 10 a minute, the caps the configuration already holds.
- **Derived (FS-12).** The confirmation's question and every spoken summary hold the listener for the clip's length, as the echo does, so `cancel` is heard between utterances and never over them.
- **Derived (TS-11).** The camera agent refuses off the NUC with `FaceContext.refusal`'s wording.
- **Derived (TS-13).** Tab is a key of the field while `App.typing()` is true, and `T` types inside the text area.
- **Derived (FS-1, AG-8).** `make launch` opens on the main menu; nothing changes in `App.run`.
- **Default.** The main menu's **Agent editor** is the tenth entry (key `0`), where **Face** was; **Word lists** follows **Agent routing** and, past the tenth, has no number, as `MenuView` already draws later entries.
- **Default.** The highlight is `theme.ok`, the theme's green in all three palettes, so no new theme role or colour pair is needed.
- **Default.** `cancel` at the root is an ordinary word: no session runs, so the router is not there to hear it.
- **Default.** The agents' notes, `about` texts and the introduce script are written by the plan's implementer in the voice of the summary panel; the owner edits the script in the Agent editor.
- **Default.** `needs.EXPECTED_SECRETS` stays as it is; the weather definition's `secret` names the same `autobot-weather-api-key`, and moving the catalogue onto the definition waits for a second secret.
- **Default.** The ten seconds a confirmation waits reuse `router.LISTEN_SECONDS`, so the two waits cannot drift apart.

## Changes needed elsewhere

- **docs/specs/active/autobot-the-face-SPEC.md, rules-first-routing-SPEC.md, agent-sessions-SPEC.md.** As the spec's "Changes needed elsewhere" lists: FS-1, FS-9, FS-13, FS-15; RR-2 and the rule form with `mode`; AS-3 and the first definition shape.
- **docs/context/SCHEMA.md.** The `[[agents]]` table as step 1 defines it (`name`, `description`, `prompt`, `confirm_entry`, `confirm_entry_question`, `confirm_request`, `confirm_request_question`, `listening`, and the owned keys `zip_code`, `daily_limit`, `minute_limit`, `secret`, `folder`, `commands`), `mode` on the rule, `agent` widened, the `[words]` table with its three lists and defaults, and `run/weather-calls.json` and `run/agents.log` under runtime state.
- **docs/context/GLOSSARY.md, ARCH.md, CONSTRAINTS.md.** As the spec lists; ARCH also names `Services.launch`, `Services.model` and `Services.weather_lookup` as seams tests fake.

## Open Questions

None.
