"""Common base for the LLM agents.

Deliberately thin: an agent is a small object holding the LLM seam and a
purpose. It keeps no mutable analysis state of its own — the orchestrator owns
the shared state and passes each agent exactly what it needs, which is what
lets agents run in parallel without stepping on each other (PRD 8.1).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.agents.llm import StructuredLLM


class Agent:
    """Base agent: a name, a model tier (for reporting/cost), and the seam."""

    name: str = "agent"
    model_tier: str = "sonnet"  # descriptive only; the real model id is the LLM's config

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def _ask(
        self,
        system: str,
        user: str,
        schema: type[BaseModel],
        *,
        temperature: float = 0.0,
    ) -> BaseModel:
        return self.llm.complete_json(
            system=system, user=user, schema=schema, temperature=temperature)
