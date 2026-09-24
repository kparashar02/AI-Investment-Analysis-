"""Agent layer — Layer 3 of the architecture (PRD 7, 8).

LLM agents that turn unstructured input (a company name, a news article, an
industry) into the **bounded, structured, scored** fields defined in
``app/models/agent_io.py``. They may reach the network and a language model;
the engine may not (``tests/unit/test_layering.py``). Every agent calls the
model through the injectable :class:`~app.agents.llm.StructuredLLM` seam, so the
whole layer is testable offline with :class:`~app.agents.llm.FakeLLM` and never
requires an API key to run the suite.

The one-way rule: agents import the engine and the models; nothing in the
engine or models imports an agent.
"""
