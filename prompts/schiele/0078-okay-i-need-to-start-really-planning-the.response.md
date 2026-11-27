---
run: schiele/0078
at: 2026-11-27T13:47:20.579Z
mode: create
agent: create
model: claude:claude-fable-5-1
target: docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md
template: create
prompt-sha: e83abe8d223a
template-sha: d7922285ccf1
commit: 331809c+
attachments: []
read:
  - AGENTS.md
  - prompts/builtin/examples/spec.md
  - docs/specs/active/autobot-the-face-SPEC.md
  - docs/context/GLOSSARY.md
  - docs/context/CONSTRAINTS.md
  - src/autobot/tui/screens/main.py
  - src/autobot/tui/router.py
  - docs/specs/active/rules-first-routing-SPEC.md
  - docs/specs/active/agent-sessions-SPEC.md
  - docs/context/SCHEMA.md
  - config/autobot.toml
  - src/autobot/tui/menu.py
  - src/autobot/tui/app.py
  - src/autobot/tui/screens/routing.py
  - docs/context/ARCH.md
  - docs/context/CONVENTIONS.md
  - src/autobot/routing.py
  - docs/context/BRIEF.md
  - src/autobot/tui/screens/face.py
  - docs/specs/active/autobot-wake-word-training-studio-SPEC.md
  - src/autobot/tui/face.py
outcome: success
usage:
  input: 273676
  output: 13702
  peak: 93903
  window: 1000000
cost: 0
---

---
type: spec
title: autobot — Agents and the agent tree
authority: prescriptive
status: active
describes:
  - src/autobot/agents/**
  - src/autobot/tui/**/agent*
  - src/autobot/tui/**/tree*
  - tests/**/*agent*
  - tests/**/*tree*
provenance:
  - run: schiele/0077
    at: 2026-11-27T13:47:20.579Z
    mode: create
---

# autobot — Agents and the agent tree

autobot's capabilities are **agents**, and the picture of them is the **agent tree**: a top-down flowchart drawn in text, with `autobot` in a box at the root and, under it, one node per agent, joined by the commands that reach them. The tree is shown twice. On the face it fills the detail panel while nothing runs, highlights where the session stands, and moves up the screen as the session goes from wake to command to agent, until the agent's own running view takes the panel over. In the **Agent editor** screen of the configuration menu the same tree is walked with the arrow keys, and Enter opens one agent's full-screen editor, where its name, description and, when it has one, its prompt are read and edited. An agent is autobot's own code: a unit with a definition, a command that reaches it, a screen it drives and, later, state of its own; some wrap a Gemini ADK agent, some answer locally with no AI at all. The first agents after the echo are `question`, `weather` (with its `forecast` alias), `time`, `camera` and `introduce`. The interface gains a **run mode**, the face, and a **configuration mode**, the main menu, and Tab switches between them. Rules AG-1 to AG-12 govern this. The spec builds on the face spec (FS-8, FS-9, FS-10, FS-13, FS-14, FS-15), the rules-first routing stub (RR-1, RR-2, RR-3, RR-4), the agent sessions stub (AS-1, AS-2, AS-4) and the studio spec (TS-1, TS-10, TS-11, TS-13); it amends FS-1, FS-9 and FS-13 as the "Changes needed elsewhere" section says and withdraws nothing else.

## Numbered rules, each with its reason

1. **AG-1. An agent is a unit of autobot's own code with a definition.** An agent is one capability autobot can be asked for. It has a **definition**, which is data: a `name`, a `description` in a sentence or two, the commands that reach it (the `[[routes]]` rules that name it, FS-15), and, for an agent that calls a language model, the `prompt` that autobot wraps around the owner's words. It has **code**, which the definition does not contain: the experience it runs on the face (FS-10) and whatever it does to answer. An agent that reasons does so through a Gemini ADK agent (AS-1), but the ADK agent is behind autobot's agent, not the agent itself: autobot's code receives the command, builds the request, processes the response in the middle and drives the screen. Every agent is an instance of the same shape, so that the tree, the editor and the router treat them alike. Reason: the owner describes each capability as "a new instance of what we would call an agent" that "could be represented by an ADK agent, but it's more than that": autobot is the code calling the ADK agent and will keep its own state (AG-11). One shape for every agent is what lets the tree and the editor show all of them, whether or not a model stands behind one.

2. **AG-2. The agent tree is a top-down flowchart: autobot at the root, agents as nodes, commands as edges.** The tree has two levels today. The root is a box reading `autobot`, the wake phrase. Under it, joined to it by lines, stand the agents, one box each, drawn in the order of their rules in `config/autobot.toml`. An edge is the command that leads from the root to the agent, and the command's word is written on it, so that the screen always shows the words that will activate each node. Two rules that reach the same agent are **aliases**: they are drawn as two edges into the one node, each labelled with its command and with what differentiates it (AG-7), so the owner sees that they share one logic and how they differ. The tree is drawn in the theme's line-drawing characters where the terminal has them and in plain ASCII where it does not, as the eyes and the banner are (FS-5). It is one model, drawn by both the face (AG-3) and the Agent editor (AG-9), and it lives in a module that does no drawing. Reason: the owner wants an "old-timey ASCII-based flowchart" as the visual cue of what autobot can do, in the way React Flow would show nodes and edges: the nodes are agents, the edges the actions that lead to them. The words on the edges are what makes the picture a hint as well as a map. One model for two screens keeps them from drifting apart.

3. **AG-3. On the face the tree fills the detail panel and follows the session.** While no experience runs, the detail panel shows the tree with the root highlighted in the theme's highlight colour (green where the theme has it): autobot is listening, and the root is the active next step. The tree then follows the session of FS-10 and FS-15:
   - **Wake.** When the listener publishes a wake, the whole chart moves up the screen so that the second level, the agents, stands where the root stood, and the agents' nodes are highlighted with their commands: the hint of what autobot can do now, while it listens for a command.
   - **Command.** When a rule matches, its agent's node is highlighted and the chart moves up again, so that the agent stands at the top.
   - **Running.** Once an agent's node is highlighted, the detail panel expands into that agent's **running view**: the screen the agent designs for itself (FS-9), shown for as long as the experience runs. The tree is not drawn under it.
   - **Done.** When the experience ends, the tree returns, with the root highlighted again.
   - **No command.** When the ten seconds of FS-15 pass without a command, the tree returns in the same way.

   Reason: the owner wants the display to show the current step it is on: the root in listening mode before the wake, the next level highlighted as a hint once the word is heard, and the agent's own screen once one is chosen. Moving the chart up the screen rather than redrawing it from the top keeps the eye on the step that is live. FS-9 already makes the detail panel the agent's; this rule says what it holds when no agent has it.

4. **AG-4. Every response is built in two parts: a spoken summary and a detail visual that fits the panel.** An agent's answer to the owner is one **TTS summary** (FS-8), the text the summary panel shows and autobot reads aloud, written to be easy to hear, and one **detail visual**, the companion that the agent puts in the detail panel, built to fit the panel as it is at that moment, not scrolled into. The agent, not the face, builds both: for an agent that calls a language model, the prompt (AG-1) asks the model for them in a shape autobot's code can take apart, and the code lays the detail out for the panel's size. The prompt and the code around it may be tuned over time; the prompt is edited in the Agent editor (AG-10), the code is not. Reason: the owner interfaces with autobot primarily by voice, and the screen is limited to what fits in the detail view. So every response must be summarised for speech and given a visual that fits, and the owner wants to tweak the prompt wrapped around the query and the logic that constructs the response as the agents mature. Tying a visual cue to every request is, in the owner's words, the most important thing, and part of what makes autobot fun to use.

5. **AG-5. The `question` agent asks a language model and answers.** The command `question` (said as "I have a question" or any sentence containing the word) routes to the first language-model agent. It takes what the owner says after it as the query, wraps it in its prompt, sends it to its Gemini ADK agent (AS-1), and answers with the two parts of AG-4: a spoken summary and a detail visual. It applies no checks and no balances beyond the routing rule that reached it: it is the basic query. The query it sends is text, the listener's local transcript (`CN-1`); no audio leaves the machine. Reason: this is the owner's first large language model agent, "to test a basic query, no checks or balances, to ask a large language model a question and receive a result", and the first exercise of the response shape of AG-4 on a real model. Validation of what is sent is the rules layer's job and comes later (RR-1); the first agent has nothing to validate against.

6. **AG-6. The `weather`, `time`, `camera` and `introduce` agents answer locally, with no language model.** These agents are what a rule can answer without an AI call (RR-2): simple things the owner always asks an assistant for and does not need one for.
   - **`weather`.** Answers with today's weather for a configured location, given as a zip code in its definition, as a short spoken narrative and a detail visual of the day. Its `forecast` alias (AG-7) opens the same agent in forecast mode, which answers with the days ahead instead of today.
   - **`time`.** Says the time, from the NUC's clock.
   - **`camera`.** Also reached by `cameras`. Opens the owner's camera application, which runs on the same host and shows his four external cameras in a web browser, by launching Chrome on the NUC's desktop to it. It is autobot's first act of desktop direction, and it runs on the NUC only: on any other machine it refuses with the one-line message the other NUC-only paths give (TS-11).
   - **`introduce`.** autobot introduces himself: who he is and how he works, in the two parts of AG-4. Its text is written in its definition and is edited as a prompt is (AG-10).

   Each has a running view of its own on the face (AG-3) and a spoken summary (AG-4). None opens a connection for reasoning; `weather` fetches its forecast, which is a lookup, not an AI call. Reason: part of the goal is "to find simple tasks that I always use that usually ask an AI agent for that I don't need an AI agent for", trapped by a word and answered at once. These four are the ones the owner named. The camera agent is the experiment he wants in controlling the PC and popping a browser, which the brief names as a secondary goal, and it touches the NUC's desktop, so it belongs under the NUC-only rule.

7. **AG-7. A rule may carry a mode, and two rules to one agent are aliases.** A routing rule (FS-15) names an agent and may also carry a **mode**, a word the agent receives with the command and uses as a parameter. Two rules that name the same agent with different modes, such as `weather` and `forecast`, are aliases of one agent: they run the same code, and the mode is what differs. The tree shows them as two labelled edges into one node (AG-2), and the Agent routing screen lists them as two rules. Reason: the owner wants "a little trick like that to invoke agents to control parameters for simple little things": saying `forecast` opens the weather agent in forecast mode. A mode on the rule gives that without a second agent, and the tree is, in his words, "a perfect use case" for showing the relationship.

8. **AG-8. Tab switches between run mode and configuration mode; the face leaves the menu and the Agent editor takes its place.** The interface has two modes. **Run mode** is the face: autobot in use. **Configuration mode** is the main menu and the screens under it: the studio, the settings, Agent routing and the Agent editor. Tab switches from one to the other from the main menu and from the face, except while a text field has the keyboard, where Tab is a key of the field (as `T` is a letter there, TS-13). The main menu no longer has a **Face** item; its place is taken by **Agent editor** (AG-9), and the menu's numbered entries are the configuration functions. The key bar on both screens shows `Tab` with the mode it leads to. `make launch` still opens on the main menu. Reason: the owner wants the face reached with the Tab key as the main mode of the application, not as a numbered menu item, and the menu kept for configuration, with the Agent editor as its new screen. Opening on the main menu follows FS-1, which keeps the studio as the opening screen until a model is trained (AG open question 1).

9. **AG-9. The Agent editor screen is the tree, walked with the arrow keys.** The **Agent editor** is a screen of the configuration menu that draws the same tree as the face does (AG-2), with the same graphics, and a selection on one node. Up and Down move the selection a level, Left and Right move it between the nodes of a level, and Enter opens the selected agent's editor view (AG-10). On the root node Enter does nothing beyond saying that the root is the wake phrase, which the Wake phrase screen edits. Reason: the owner wants the editor "to look just like the flowchart that we're going to display in the face app", moved through with the arrow keys as a tree that has two levels for now.

10. **AG-10. The agent editor view shows how an agent is built and edits its definition, not its code.** Enter on an agent opens a full-screen view of that agent. It shows the definition of AG-1: the name, the description, the commands and modes that reach it, whether a language model stands behind it, and, for an agent that has one, the prompt in a multi-line text field. It also says what the agent's code does, as text, so that the owner can see how the agent is built. The name, the description and the prompt are edited here by typing; every edit is written to the definition's place in `config/autobot.toml` at once (TS-10). The commands are shown here and edited on the Agent routing screen, which writes the same `[[routes]]` rules. The view does not edit code: an agent's experience and the logic around its prompt are changed in the repository. Reason: the owner wants a place in the configuration application "to see and edit at least the metadata for the agents we have configured so far", and in particular the prompt, since "we usually will wrap our queries around a prompt to give it the context we need", while being clear that "this interface is not intended to edit the code that an agent is running".

11. **AG-11. Agents keep their state on this machine, in this layer.** When an agent keeps state between sessions, the state lives on the NUC (AS-2, ADR 0002), is owned by autobot's agent code of AG-1 rather than by the ADK agent behind it, and is kept beside the agent's definition once a shape for it is fixed. No agent of this spec keeps state yet; this rule names where it goes when one does. Reason: the owner says "right now, we're not keeping state, but we're going to, this is the layer when we do where we will, because we're going to be processing all of the responses in the middle". Saying so now stops the first stateful agent from putting its state somewhere else.

12. **AG-12. Speech stays local, only text leaves, and the logic is testable without a terminal.**
    - **Speech.** Every agent hears through the listener's local transcripts and speaks through the face's local piper voice (FS-13, `CN-1`, `CN-2`). No agent sends audio anywhere.
    - **Network.** The face opens a network connection only on an agent's behalf: the `question` agent's reasoning, as text, to Gemini in the owner's Google Cloud project (AS-1), and the `weather` agent's lookup. The echo, `time`, `camera` and `introduce` open none.
    - **Testable.** The tree model, the agents' response shaping (AG-4), the mode handling (AG-7) and the editor's logic live in modules that do no drawing and are tested with fakes for the model, the lookup and the browser launch, with no microphone, speaker or network (FS-14).
    - **Secrets.** The credentials the `question` agent needs are not in the repository and not in `config/autobot.toml`; a missing credential is a one-line reason in the summary panel with the *trouble* reaction, not a traceback.

    Reason: `CN-1` and `CN-2` forbid any cloud speech path, and ADR 0002 has the agents reason in the cloud with everything else local. FS-13 says the face opens no network connection, written when the echo was the only agent; the first agent that reasons needs the one exception, and naming it keeps the rest closed. `CONVENTIONS.md` "Testing" and "Control layers" require the logic to be testable without a terminal and failures to be one line. `AGENTS.md` keeps the Google Cloud credentials out of the repository.

## Settled without asking

- **Derived (RR-1, RR-3, FS-15).** An agent is reached only by a routing rule that names it; the tree's edges are those rules.
- **Derived (RR-2).** `weather`, `time`, `camera` and `introduce` are local answers with no AI call, since the owner named them as things he does not need an AI agent for.
- **Derived (AS-1).** The `question` agent reasons through a Gemini ADK agent against Gemini in the owner's Google Cloud project.
- **Derived (AS-2, ADR 0002).** Agent state, when it comes, lives on the NUC with autobot's own agent code, not in the ADK agent (AG-11).
- **Derived (FS-8, AS-4).** The spoken part of a response is the TTS summary the face already defines; AG-4 adds the detail visual beside it.
- **Derived (TS-11, CONVENTIONS "Control layers").** The `camera` agent, which drives the NUC's desktop, refuses to run on any other machine.
- **Derived (TS-10, CONVENTIONS "Layout").** Agent definitions are written to `config/autobot.toml`, the one place configuration lives, as `[[routes]]` already is (open question 2 asks whether that holds for long prompts).
- **Derived (FS-5).** The tree is drawn in line-drawing characters where the terminal has them and in plain ASCII otherwise.
- **Derived (FS-1).** `make launch` still opens on the main menu, in configuration mode (open question 1).
- **Default.** The second level of the tree is drawn in the order of the rules in `config/autobot.toml`, which is the order the router tries them, so the picture and the routing agree.
- **Default.** The ten-second wait and the no-command ending of FS-15 return the tree to its idle state with the root highlighted; the transcript described the happy path only.
- **Default.** A rule's parameter is called a **mode**, the owner's word for "forecast mode"; `weather` is the default mode and `forecast` the alias.
- **Default.** The Agent routing screen stays, and remains where commands are edited; the Agent editor shows them (open question 5).
- **Default.** The `introduce` agent's text is written in its definition and edited as a prompt is, so that it needs no language model (open question 4).
- **Default.** Tab is a field key while a text field has the keyboard, by the rule the interface already applies to `T` (TS-13).
- **Default.** The rule prefix is `AG`; no document uses it.

## Changes needed elsewhere

- **docs/specs/active/autobot-the-face-SPEC.md.** FS-1: the face is reached by Tab from the main menu, not as a menu item (AG-8). FS-9: while no experience runs the detail panel shows the agent tree (AG-3). FS-13: "the face opens no network connection" gains the exception of AG-12 for an agent's reasoning and lookups, as text. FS-15: a rule may carry a mode (AG-7).
- **docs/specs/active/rules-first-routing-SPEC.md.** Record the first local answers of RR-2 (`time`, `weather`, `camera`, `introduce`) and the mode on a rule (AG-7). Answer the open question on the form of a rule so far: `command`, `agent`, optional `mode`.
- **docs/specs/active/agent-sessions-SPEC.md.** AS-3 binds agents to wake phrases; agents are bound to commands after the one wake phrase (FS-10, AG-1). Record the first agent definition shape (AG-1) and the `question` agent as the first session.
- **docs/context/SCHEMA.md.** Define the **Agent definition** shape of AG-1 (`name`, `description`, `prompt`) in `config/autobot.toml`, add `mode` to the **Rule** table, widen `agent` beyond `echo`, and answer the open question on where agent definitions live.
- **docs/context/GLOSSARY.md.** Add "agent tree", "node", "edge", "alias", "mode", "running view", "detail visual", "run mode", "configuration mode" and "Agent editor". The **agent** entry says an agent is reached through wake phrases; it is reached by a command, and it is autobot's own code around an optional ADK agent (AG-1).
- **docs/context/ARCH.md.** The rules layer and the agent host are no longer "Not built" once the `question` agent lands; add the Agent editor and the two modes to the interface's screens; note that the **Spoken response** seam has the two-part response of AG-4; answer the open question on where the rules layer and the agents appear in the interface.
- **docs/context/CONSTRAINTS.md.** Its open question asks whether `CN-1` permits sending the finished transcript to the cloud for reasoning. The `question` agent does so (AG-5, AG-12); the answer should be recorded.
- **AGENTS.md.** The interface's screens gain the Agent editor and lose Face from the menu; Tab opens the face. The list of agents gains `question`, `weather`, `time`, `camera` and `introduce`.
- **README.md.** "Running it" says Tab opens the face and names the first commands to try.
- **CHANGELOG.md.** Record the agent tree, the Agent editor, the two modes and Tab, the mode on a rule and the new agents under Unreleased.

## Open Questions

- **Which mode opens on launch.** The owner calls the face "the main mode for this application", but FS-1 opens on the studio's main menu until a model is trained. Should `make launch` open in run mode once a model is installed, or always in configuration mode? Recommended: configuration mode always, until the owner asks otherwise, because FS-1 chose it and one Tab reaches the face.
- **Where long prompts live.** AG-10 writes a prompt to `config/autobot.toml`, the one configuration file. A prompt can run to many lines, and `SCHEMA.md` leaves open whether agent definitions join that file. Recommended: `[[agents]]` tables in `config/autobot.toml` with multi-line strings, because the layout convention admits no second store and TOML carries multi-line text.
- **The weather source and the location.** The weather agent "just looks up the weather" for a configured zip code. Which service answers the lookup, and is the zip code the whole of the location setting? Recommended: a free forecast API that needs no account, with the zip code as the only setting, because the agent exists to be quick and to need no AI.
- **The camera application.** The camera agent launches Chrome to an application on the same host. What is its address, and should the agent close the browser when the owner is done? Recommended: the address as a setting in the agent's definition and no closing, because the transcript names only the opening.
- **Introduce without a model.** Should `introduce` speak a written text, or should it ask the language model to introduce autobot each time? Recommended: a written text in its definition, because the owner listed it among the simple agents and a written text is the same every time and costs nothing.
- **The Agent routing screen.** With the Agent editor showing each agent's commands, does the Agent routing screen stay as the place commands are edited, or fold into the editor? Recommended: it stays, because it also tries a typed sentence against the rules, which the editor does not.
