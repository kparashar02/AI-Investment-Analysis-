"""Agentic AI Equity Research Analyst.

Layer boundary, enforced by convention and by test:

* ``app/data``   — sources. Provide facts.
* ``app/engine`` — deterministic computation. Perform calculation. **Must not
  import from app/agents.** ``tests/unit/test_layering.py`` asserts this.
* ``app/agents`` — LLM agents. Perform analysis on unstructured input.
* ``app/report`` — rendering.

See docs/METHODOLOGY.md and PRD.md section 6 for why the boundary exists.
"""

__version__ = "0.1.0"
