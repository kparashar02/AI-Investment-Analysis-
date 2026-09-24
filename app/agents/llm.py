"""The LLM seam (PRD 6, 12.7).

Every agent talks to the model through :class:`StructuredLLM` — a tiny
interface that takes a system prompt, a user prompt and a Pydantic output
schema, and returns a validated instance of that schema. Two things follow from
that shape, both deliberate:

* **Structured output only.** An agent never receives free prose it then has to
  parse; it receives a schema-validated object whose every field was range-
  checked (``app/models/agent_io.py``). A model that returns an out-of-range
  materiality fails validation here, at the boundary, rather than corrupting a
  score downstream.
* **The seam is injectable.** Tests pass :class:`FakeLLM` with canned responses,
  so the whole agent layer runs offline with no API key and no network — the
  same discipline the yfinance provider gets. The real client,
  :class:`OpenRouterLLM`, is only constructed when an analysis actually runs.

LLM calls run at ``temperature 0`` and request JSON, which — together with the
bounded discrete fields — is what keeps the pipeline as close to deterministic
as an LLM allows (PRD 12.7).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


class LLMError(Exception):
    """Base class for LLM failures an agent should surface, not swallow."""


class LLMUnavailable(LLMError):
    """The LLM cannot be used — no API key configured, or the client library
    is missing. Distinct from a call that was attempted and failed."""


class LLMResponseError(LLMError):
    """The model replied, but the reply could not be validated against the
    requested schema after a repair attempt."""


@runtime_checkable
class StructuredLLM(Protocol):
    """The single method every agent uses to consult the model."""

    def complete_json(
        self, *, system: str, user: str, schema: type[T], temperature: float = 0.0
    ) -> T:
        """Return a validated ``schema`` instance from the model's JSON reply."""
        ...


def _extract_json(text: str) -> str:
    """Pull the first JSON object out of a model reply.

    Free models often wrap JSON in prose or ```json fences; this finds the
    outermost brace-balanced object so a chatty model still parses."""
    fenced = text.strip()
    if "```" in fenced:
        # take the content of the first fenced block
        parts = fenced.split("```")
        for part in parts:
            candidate = part[4:] if part.lower().startswith("json") else part
            if "{" in candidate:
                fenced = candidate
                break
    start = fenced.find("{")
    if start == -1:
        raise LLMResponseError("no JSON object found in model reply")
    depth = 0
    for i in range(start, len(fenced)):
        if fenced[i] == "{":
            depth += 1
        elif fenced[i] == "}":
            depth -= 1
            if depth == 0:
                return fenced[start : i + 1]
    raise LLMResponseError("unbalanced JSON braces in model reply")


class FakeLLM:
    """Offline test double. Returns queued responses in order.

    Each queued item may be a Pydantic model, a dict, or a JSON string; it is
    validated against the schema the caller asks for, exercising the same
    validation path as a real reply. Every call is recorded on ``calls`` so a
    test can assert what the agent asked."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._queue: list[Any] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, *responses: Any) -> "FakeLLM":
        self._queue.extend(responses)
        return self

    def complete_json(
        self, *, system: str, user: str, schema: type[T], temperature: float = 0.0
    ) -> T:
        self.calls.append({"system": system, "user": user, "schema": schema.__name__,
                           "temperature": temperature})
        if not self._queue:
            raise LLMError(f"FakeLLM has no queued response for {schema.__name__}")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item              # let a test simulate a failed model call
        if isinstance(item, schema):
            return item
        if isinstance(item, BaseModel):
            return schema.model_validate(item.model_dump())
        if isinstance(item, str):
            return schema.model_validate_json(item)
        return schema.model_validate(item)


class RoutedFakeLLM:
    """Offline test double that answers by output-schema, not call order.

    The orchestrator runs several agents in parallel supersteps, so a single
    FIFO queue would be order-dependent and flaky. This double keys queued
    responses by ``schema.__name__`` and pops from that schema's list, so the
    order agents happen to run in does not matter. A schema with no (remaining)
    response raises :class:`LLMError`, which agents that can degrade will catch.
    """

    def __init__(self, responses_by_schema: dict[str, list[Any]] | None = None) -> None:
        self._by_schema: dict[str, list[Any]] = {
            k: list(v) for k, v in (responses_by_schema or {}).items()
        }
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, schema: type[T], temperature: float = 0.0
    ) -> T:
        self.calls.append({"schema": schema.__name__, "user": user})
        queue = self._by_schema.get(schema.__name__)
        if not queue:
            raise LLMError(f"RoutedFakeLLM has no queued response for {schema.__name__}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, schema):
            return item
        if isinstance(item, BaseModel):
            return schema.model_validate(item.model_dump())
        if isinstance(item, str):
            return schema.model_validate_json(item)
        return schema.model_validate(item)


class OpenRouterLLM:
    """Real client — OpenRouter's OpenAI-compatible chat completions over httpx.

    Configured from the environment (``OPENROUTER_API_KEY``, ``OPENROUTER_MODEL``,
    ``OPENROUTER_BASE_URL``) so a free model can be used in development and
    swapped for a paid one by changing one variable. httpx is imported lazily,
    so importing this module — and running the test suite — costs nothing and
    needs no key."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_repair_attempts: int = 1,
        max_rate_retries: int = 2,
        requests_per_minute: float = 18.0,
        http_client: Any = None,
    ) -> None:
        from app.data.rate_limiter import TokenBucket  # noqa: PLC0415

        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_repair_attempts = max_repair_attempts
        self.max_rate_retries = max_rate_retries
        # Pace calls proactively under the free-tier per-minute limit (default 20/min)
        # so we avoid 429s rather than burn the daily quota retrying them.
        self._bucket = TokenBucket(capacity=3, refill_rate=max(0.1, requests_per_minute / 60.0))
        self._http = http_client  # injectable for tests; else a lazy httpx client

    def available(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        try:
            import httpx  # noqa: PLC0415 — lazy; keeps the suite dependency-free
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("httpx is not installed; needed for OpenRouter calls") from exc
        self._http = httpx.Client(timeout=self.timeout)
        return self._http

    def _post(self, messages: list[dict[str, str]]) -> str:
        if not self.available():
            raise LLMUnavailable("OPENROUTER_API_KEY is not set")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": "Equity Research Analyst",
            "Content-Type": "application/json",
        }
        response = self._request_with_backoff(payload, headers)
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"unexpected OpenRouter response shape: {data}") from exc

    def _request_with_backoff(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        """POST with retry on rate limits / transient server errors.

        Free models rate-limit aggressively, returning HTTP 429. This waits
        (honouring a ``Retry-After`` header, else exponential backoff with
        jitter) and retries, which is what makes a free model usable across a
        pipeline of many calls."""
        import random  # noqa: PLC0415
        import time  # noqa: PLC0415

        url = f"{self.base_url}/chat/completions"
        client = self._client()
        self._bucket.acquire()   # proactive pacing to stay under the per-minute limit
        for attempt in range(self.max_rate_retries + 1):
            response = client.post(url, json=payload, headers=headers)
            if response.status_code in (429, 502, 503) and attempt < self.max_rate_retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 0.0
                except ValueError:
                    wait = 0.0
                wait = max(wait, min(2.0 ** attempt, 30.0)) + random.uniform(0.0, 1.5)
                time.sleep(wait)
                continue
            return response
        return response

    def complete_json(
        self, *, system: str, user: str, schema: type[T], temperature: float = 0.0
    ) -> T:
        schema_hint = json.dumps(schema.model_json_schema())
        system_full = (
            f"{system}\n\nReturn ONLY a JSON object that validates against this "
            f"JSON Schema. No prose, no markdown fences.\nSchema:\n{schema_hint}"
        )
        messages = [{"role": "system", "content": system_full}, {"role": "user", "content": user}]

        last_error: Exception | None = None
        for attempt in range(self.max_repair_attempts + 1):
            content = self._post(messages)
            try:
                return schema.model_validate_json(_extract_json(content))
            except (ValidationError, LLMResponseError, json.JSONDecodeError) as exc:
                last_error = exc
                # Feed the error back for a single repair attempt.
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"That did not validate: {exc}. Return corrected JSON only.",
                })
        raise LLMResponseError(f"model output failed schema validation: {last_error}")


def default_llm() -> StructuredLLM:
    """Construct the configured real client. Raises :class:`LLMUnavailable` at
    call time if no key is set, never at import."""
    return OpenRouterLLM()
