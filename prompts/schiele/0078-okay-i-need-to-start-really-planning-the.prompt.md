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

Okay, I need to start. Really planning the functionality I'm going to want to build this thing to support, and how I'm going to show it. I think right now the detail view in the face application needs to be like an old-timey ASCII-based flowchart of what it's capable of as like a visual cue showing what it's capable of. So starting from the top down. So it will be a top-down flowchart starting with Autobot. And a square, and then down from there is going to be another tree of connected nodes, one of which is Echo. And I need to think about what a realistic next level would be to route the kinds of things that I want to do. So it's a little bit of chicken egg in the design right now.

tool, and I just kind of sit here in brainstorm the kinds of things I want it to do. Like the next thing on the list will be word question. So I'll tell-I have a question. And that is going to be my first large language model, called, it's just going to test a basic query, no checks or balances, to ask a large language model a question and receive a result. And then summarize it for best. So there needs to be a voice summary. So essentially the response itself, because we are interfacing with primarily by voice, and the screen itself is going to be limited to what we can fit in the detail view. We need to take every response and fit it into the, not fit it, but yeah. Build appropriate visual response in the detail view that will fit in the screen and rise a response, parameters that make it easy to read back by the next speech, which is the basic query, and then we can tweak over time the prompt that we wrap around it, the logic that we use to construct the response. There's going to be very important pieces of code, so each one of these codes should be represented by essentially a new, if it was an object-oriented library, it would be a new instance of what we would call an agent that could absolutely, on the back end, be represented by an ADK agent, but it's more than that because we are code that is calling that ADK agent, we will keep our own state. Right now, we're not keeping state, but we're going to, this is the layer when we do where we will, because we're going to be processing all of the responses in the middle.

and frankly, if I were building this in next JS, I would be using React Flow here to show the relationship between the root Autobot, wake script and the subsequent downstream commands that it can do as nodes and edges. So the nodes, the agent, the edges, the action that ties us to the next agent which itself has an upstream-grounded behavior.

In this application we can show them in a custom flowchart view, but I want the user, I want the display to show the current step it's on. So we'll see the tree will be sitting with the autobot node, which will be shown in the listening mode, probably color green or something, showing that it's the active next opportunity, if I'm looking at the screen, it hasn't woken up yet in the moment it hears the wake-word, then the screen should shift to the next level, so it should essentially move the entire chart, flow chart up the screen so that the next level of the tree is highlighted and it would display each of the nodes on the screen as a hint, the currently configured tasks it's capable of and once one of those words is matched the exact same thing happens it should highlight, then shift, once it's actually highlighted an agent node, then the entire detail screen should expand to show a detail view, an active running view of that node agent so that would be the running screen as long as it's running that detail view should show a custom screen that we designed for that agent we manage the workflow

So that's basically how I want the screen to show the agents, and I probably need a list of kind of the next agents that I have in mind, and I haven't really thought this through much common asks that will be top-level tasks, because part of the goal here is to find simple tasks that I always use that usually ask an AI agent for that I don't need an AI agent for that I can just look up if I trap the word here, so things like weather, and the weather should just look up the weather from where I am right now that's in I can, that agent I can have a configured location like the zip code and it can just very quickly respond with a forecast appropriate for a primarily audio interface but we can use the screen remember because the agent will have the space on the detail screen to show a companion visual with every request that's the most important thing is tying a bit of a visual cue and using the interface and making it a little fun frankly too because that's a core value this application is making it fun to use so if I say, hey autobot and then what's the weather then it should very quickly be able to build a narrative explaining what the weather is for me today and detail view to think through how to pass parameters to it because clearly just saying what's the weather might not be enough because usually I say what's the weather outside meaning today or what's the weather going to be that's another kind of request that I ask all the time that may be a little harder I can probably even make that a there's a neat idea kind of agent that is a the same agent but it adds a parameter so if I say the word forecast it opens the weather agent but it opens it in a forecast mode whereas default weather is what it is today we can make a little trick like that to invoke agents to control parameters for simple little things like that

and that is a perfect use case for the flowchart view. It should always show the words on the screen that will activate it or differentiate it. So the weather node should have, if we're going to enable a forecast alias, it should show both and show their relationship as aliases to the same logic, but what differentiates them so that I can see what those next commands that it supports are.

otherwise, time, right, what time it is, that's obvious, no one to have one, my cameras, cameras, actually probably do camera and camera, essentially I have four external cameras that I have written an application on the same host that's running this application, that run on a web browser that shows all four cameras in my house, so I will write an application to launch and invoke that, that'll be a custom little application, that will actually be an interesting experiment to see if I can control the actual PC and pop Chrome browser.

I want a fun little bot based on the word introduce, where I can ask autobot to introduce himself, and he will explain what he's about and how he works.

tool, so based on that small description of a few kinds of agents I want to build, I can also envision dozens more kinds, deep applications built behind them, deep agent integrations built behind them, most of which is going to be custom application code, but we're still going to need a place in the Autobot configuration application to see and edit at least the metadata for the agents we have configured so far. So right now there is, I run the agent configuration app, there is one through zero options as basic menu choices, and if I check the pick zero, I launch the face app. I would rather have that application be all of the core application functions one through nine already are the basic configuration one, and the face app is actually, I want to access with the tab key, and it's basically a mode, it's the main mode for this application, we hit tab, we're in run mode, we hit tab again, and it defaults to the configuration mode, we're in configuration mode, and tab actually launches the face app, and the face app is the main app. But when we're in the configuration menu, the main menu is fine for the configuration menu, we need another screen option to display the agent, we call it the agent editor, even though we clearly can't rewrite the code that the agents are running, but any of them that are based on a large language model will have a prompt that we can view and edit here, because we usually will wrap our queries around a prompt to give it the context we need, so, and there's normally some code that we write around it, but the prompt itself is editable, so we're going to want to be able to see the list of agents and then edit their prompts, but I don't want it in this same, yeah, we could just make it a menu item, agent editor, actually, and so take face out of there, maybe replace it with agent editor, where tab is, opens face,

and the agent editor I want to look just like the flowchart that we're going to display in the face app. We can use the same graphics maybe, but the arrow keys will allow me to nodes, a row down a row left and right between nodes, basically just a tree, which only has two levels right now, and the hitting enter on it opens a full-screen agent editor view. And it will mostly show information about how the agent is built to do because this interface is not intended to edit the code that an agent is running, but if there's a prompt involved, it can show an editor for that. And basic stuff like what it's called, the name, descriptions, things like that, we can edit it in here by hand. Just by typing if we want to. So that's the interface that we can use to edit the agents.

okay, let's capture all of this in a spec now.
