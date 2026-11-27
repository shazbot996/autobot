"""The one shape every agent is an instance of (AG-1). No drawing.

An `Agent` is autobot's own code for one capability. Its definition is data
(`autobot.config.AgentConfig`, one `[[agents]]` table), and what the code
does is said in `about`, for the Agent editor (AG-10), since the definition
does not contain the code. When a rule routes a command to an agent, the
router hands it a `Launch` and the agent makes the `Experience` it runs on
the face (FS-10). An agent that reasons does so through a Gemini ADK agent
behind it, but the ADK agent is not the agent: autobot's code receives the
command, builds the request, processes the response and drives the screen.

State. No agent keeps state yet. When one does, the state lives on the NUC,
beside the agent's definition, and is owned by the agent's code here, not
by the ADK agent behind it (AG-11, ADR 0002). It is never moved off the
machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from autobot.config import AgentConfig
from autobot.tui.face import Experience


@dataclass(frozen=True)
class Launch:
    """What the router hands an agent when a command starts it."""

    # The agent's definition, from `config/autobot.toml`.
    definition: AgentConfig
    # The mode the matched rule carries (AG-7); empty when it carries none.
    mode: str
    # The transcript the rule matched in.
    transcript: str


class Agent(Protocol):
    """One capability of autobot: its code, behind a definition (AG-1)."""

    @property
    def name(self) -> str:
        """One of `autobot.config.ROUTE_AGENTS`: the definition's `name`."""

    @property
    def about(self) -> str:
        """What the agent's code does, in the voice of the summary panel (AG-10)."""

    @property
    def uses_model(self) -> bool:
        """Whether a language model stands behind the agent."""

    @property
    def sends_requests(self) -> bool:
        """Whether it sends a request off the machine, so the before-a-request
        confirmation is offered (AG-13)."""

    @property
    def settings(self) -> tuple[str, ...]:
        """The definition keys the agent owns beyond every agent's, for the editor."""

    def make(self, launch: Launch) -> Experience:
        """The experience the agent runs on the face for this command (FS-10)."""
