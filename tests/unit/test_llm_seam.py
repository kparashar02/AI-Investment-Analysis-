"""The LLM seam (app/agents/llm.py) — FakeLLM and OpenRouterLLM, offline."""

from __future__ import annotations

import pytest

from app.agents.llm import (
    FakeLLM,
    LLMError,
    LLMResponseError,
    OpenRouterLLM,
    _extract_json,
)
from app.models.agent_io import CompanyIdentity


# --- FakeLLM ---------------------------------------------------------------

def test_fake_llm_validates_a_dict_into_the_schema():
    llm = FakeLLM([{"ticker": "TCS.NS", "legal_name": "Tata Consultancy Services"}])
    out = llm.complete_json(system="s", user="u", schema=CompanyIdentity)
    assert isinstance(out, CompanyIdentity)
    assert out.ticker == "TCS.NS"


def test_fake_llm_accepts_a_model_instance():
    identity = CompanyIdentity(ticker="INFY.NS", legal_name="Infosys")
    llm = FakeLLM([identity])
    out = llm.complete_json(system="s", user="u", schema=CompanyIdentity)
    assert out.ticker == "INFY.NS"


def test_fake_llm_records_calls():
    llm = FakeLLM([{"ticker": "X", "legal_name": "X"}])
    llm.complete_json(system="sys", user="usr", schema=CompanyIdentity)
    assert llm.calls[0]["schema"] == "CompanyIdentity"
    assert llm.calls[0]["temperature"] == 0.0


def test_fake_llm_empty_queue_raises():
    with pytest.raises(LLMError):
        FakeLLM([]).complete_json(system="s", user="u", schema=CompanyIdentity)


# --- JSON extraction -------------------------------------------------------

def test_extract_plain_json():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_from_fenced_block():
    text = 'Here you go:\n```json\n{"a": 1, "b": 2}\n```\nDone.'
    assert _extract_json(text) == '{"a": 1, "b": 2}'


def test_extract_json_from_prose():
    assert _extract_json('The answer is {"x": {"y": 1}} okay') == '{"x": {"y": 1}}'


def test_extract_json_missing_raises():
    with pytest.raises(LLMResponseError):
        _extract_json("no json here")


# --- OpenRouterLLM with an injected fake transport -------------------------

class _FakeResponse:
    def __init__(self, content: str, status_code: int = 200):
        self._content = content
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeHttp:
    """Returns queued reply contents in order, recording each POST."""

    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.posts: list[dict] = []

    def post(self, url, json, headers):  # noqa: A002 - mirrors httpx signature
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(self._contents.pop(0))


def test_openrouter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert OpenRouterLLM(api_key="").available() is False


def test_openrouter_parses_valid_json():
    http = _FakeHttp(['{"ticker": "TCS.NS", "legal_name": "TCS"}'])
    llm = OpenRouterLLM(api_key="k", model="free/model", http_client=http)
    out = llm.complete_json(system="resolve", user="TCS", schema=CompanyIdentity)
    assert out.ticker == "TCS.NS"
    # Determinism + structured output are requested on the wire.
    payload = http.posts[0]["json"]
    assert payload["temperature"] == 0.0
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "free/model"
    # The schema is handed to the model.
    assert "Schema" in payload["messages"][0]["content"]


def test_openrouter_repairs_once_then_succeeds():
    http = _FakeHttp(["not json at all", '{"ticker": "INFY.NS", "legal_name": "Infosys"}'])
    llm = OpenRouterLLM(api_key="k", http_client=http, max_repair_attempts=1)
    out = llm.complete_json(system="s", user="u", schema=CompanyIdentity)
    assert out.ticker == "INFY.NS"
    assert len(http.posts) == 2   # original + one repair


def test_openrouter_retries_on_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)   # no real waiting

    class _RateLimitedHttp:
        def __init__(self, responses):
            self._responses = list(responses)
            self.posts = 0

        def post(self, url, json, headers):  # noqa: A002
            self.posts += 1
            content, status = self._responses.pop(0)
            return _FakeResponse(content, status_code=status)

    http = _RateLimitedHttp([("", 429), ("", 429),
                             ('{"ticker": "X", "legal_name": "X"}', 200)])
    llm = OpenRouterLLM(api_key="k", http_client=http, max_rate_retries=3)
    out = llm.complete_json(system="s", user="u", schema=CompanyIdentity)
    assert out.ticker == "X"
    assert http.posts == 3        # two 429s, then success


def test_openrouter_gives_up_after_repair_budget():
    http = _FakeHttp(["nope", "still nope"])
    llm = OpenRouterLLM(api_key="k", http_client=http, max_repair_attempts=1)
    with pytest.raises(LLMResponseError):
        llm.complete_json(system="s", user="u", schema=CompanyIdentity)
