"""autobot's agents: the capabilities a command after the wake hands work to.

An agent is autobot's own code with a definition (AG-1): the definition is
data, one `[[agents]]` table of `config/autobot.toml`
(`autobot.config.AgentConfig`); the code is the experience it runs on the
face. Every agent is an instance of one shape, `base.Agent`, so the router,
the tree and the editor treat them alike. `registry.AGENTS` holds them by
name.

Nothing here draws. `response` shapes an answer in two parts, a spoken
summary and a detail visual that fits the panel (AG-4), and `speech` is the
one way an agent speaks a line through the face's local voice (`CN-2`).
"""
