"""The layer boundary (PRD NF15, and section 6 of the PRD).

``app/engine/`` is the deterministic layer. The separation of concerns that
the whole design rests on is only real if it is enforced, so this test reads
the engine's source and fails if it ever imports an agent, an LLM client or a
network library.

It is a cheap test that protects an expensive claim. Once the engine can call
a language model, "the recommendation is computed, not generated" stops being
true, and no amount of documentation restores it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config.settings import REPO_ROOT

pytestmark = pytest.mark.layering

ENGINE_DIR = REPO_ROOT / "app" / "engine"

FORBIDDEN_PREFIXES = (
    "app.agents",       # the agent layer
    "anthropic",        # LLM clients
    "openai",
    "langchain",
    "langgraph",
    "httpx",            # network
    "requests",
    "urllib.request",
    "socket",
    "random",           # non-determinism
    "secrets",
    "uuid",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def engine_files() -> list[Path]:
    return sorted(ENGINE_DIR.glob("*.py"))


def test_engine_directory_is_not_empty():
    assert engine_files(), "no engine modules found — has the layout changed?"


@pytest.mark.parametrize("path", engine_files(), ids=lambda p: p.name)
def test_engine_module_imports_nothing_forbidden(path: Path):
    for module in _imported_modules(path):
        for forbidden in FORBIDDEN_PREFIXES:
            assert not (module == forbidden or module.startswith(forbidden + ".")), (
                f"{path.name} imports '{module}'. The engine is the deterministic "
                f"layer: it may not reach an agent, an LLM client, the network, or "
                f"a source of randomness. See PRD section 6."
            )


def test_models_do_not_import_agents():
    """The data contracts are shared, so they must not depend on the agent layer."""
    for path in sorted((REPO_ROOT / "app" / "models").glob("*.py")):
        for module in _imported_modules(path):
            assert not module.startswith("app.agents"), f"{path.name} imports {module}"


def test_investment_decision_schema_has_no_llm_writable_field():
    """Schema-level enforcement of the separation of concerns.

    The rating is produced by the scoring engine. There is deliberately no
    field on the decision object that a narrative agent could write, so even
    a successful prompt injection cannot alter a recommendation.

    ``InvestmentDecision`` arrives with the Phase 2 engine; until then this
    test documents the requirement and skips.
    """
    try:
        from app.models.decision import InvestmentDecision  # noqa: PLC0415
    except ImportError:
        pytest.skip("InvestmentDecision lands with the Phase 2 decision engine")

    fields = set(InvestmentDecision.model_fields)
    narrative_fields = {"thesis", "commentary", "narrative", "rationale_text", "summary"}
    assert not (fields & narrative_fields), (
        "InvestmentDecision must carry no free-text field an agent could write; "
        "narrative belongs on ResearchReport, which cannot change the rating."
    )
