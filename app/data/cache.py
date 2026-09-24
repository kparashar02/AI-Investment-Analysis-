"""Persistent disk cache with per-source TTL (PRD 9.2).

Caching is not a performance nicety here; it is what makes the system usable
at all on metered free-tier APIs, and what makes a live demo reproducible
regardless of the network. The cache stores the *raw provider payload* exactly
as it was fetched, so:

* a re-run inside the TTL costs zero API calls and returns byte-identical data,
  which is what the determinism guarantee (PRD NF4) ultimately rests on; and
* if the normalisation mapping is later corrected, cached responses can be
  re-normalised without re-fetching.

TTLs differ by the staleness profile of the data (PRD 9.2): a quote goes stale
in minutes, an annual statement in a month. They are keyed by a logical
*kind*, not by source, so every provider of the same kind shares one policy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.config.settings import REPO_ROOT

# Per-kind time-to-live, in seconds. Straight from PRD 9.2's staleness table.
DEFAULT_TTLS: dict[str, int] = {
    "annual_statements": 30 * 24 * 3600,  # change only on an annual filing
    "quarterly_statements": 7 * 24 * 3600,
    "statements": 30 * 24 * 3600,  # a combined annual+quarterly fetch
    "quote": 15 * 60,  # market data moves minute to minute
    "market": 15 * 60,
    "news": 6 * 3600,
    "industry": 7 * 24 * 3600,
}

# The fallback TTL for a kind not named above: conservative, so an unclassified
# response is treated as short-lived rather than served stale for a month.
DEFAULT_TTL = 15 * 60


def default_cache_dir() -> Path:
    """The on-disk cache root. Overridable via ``CACHE_DIR`` in the environment
    so a test can point it at a scratch directory (PRD .env.example)."""
    import os

    configured = os.environ.get("CACHE_DIR")
    if configured:
        return Path(configured)
    return REPO_ROOT / "data" / "cache"


def _safe_segment(value: str) -> str:
    """Make a cache-key segment safe to use as a filename across platforms."""
    keep = []
    for ch in value:
        keep.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    return "".join(keep) or "_"


class DiskCache:
    """A JSON file cache under ``root``, namespaced ``root/<source>/<kind>/<key>.json``.

    Each entry is wrapped with the time it was stored and the TTL it was
    stored under, so :meth:`get` can decide freshness from the file alone and a
    later TTL change does not retroactively resurrect an entry that was already
    expired when written.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        ttls: dict[str, int] | None = None,
        enabled: bool = True,
        clock: Any = time.time,
    ) -> None:
        self.root = Path(root) if root is not None else default_cache_dir()
        self.ttls = {**DEFAULT_TTLS, **(ttls or {})}
        self.enabled = enabled
        self._clock = clock

    def ttl_for(self, kind: str) -> int:
        return self.ttls.get(kind, DEFAULT_TTL)

    def _path(self, source: str, kind: str, key: str) -> Path:
        return (
            self.root
            / _safe_segment(source)
            / _safe_segment(kind)
            / f"{_safe_segment(key)}.json"
        )

    def get(self, source: str, kind: str, key: str) -> dict[str, Any] | None:
        """Return the cached payload if present and still fresh, else ``None``.

        A malformed or unreadable entry is treated as a miss rather than an
        error: a corrupt cache file should degrade to a re-fetch, never crash
        an analysis.
        """
        if not self.enabled:
            return None
        path = self._path(source, kind, key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                entry = json.load(handle)
            stored_at = float(entry["stored_at"])
            ttl = float(entry["ttl"])
            payload = entry["payload"]
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            return None
        if self._clock() - stored_at > ttl:
            return None
        return payload

    def set(
        self, source: str, kind: str, key: str, payload: dict[str, Any]
    ) -> None:
        """Store ``payload`` under the TTL configured for ``kind``.

        Writes atomically via a temp file and replace, so a crash mid-write can
        never leave a half-written entry that would later read as a corrupt hit.
        """
        if not self.enabled:
            return
        path = self._path(source, kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "stored_at": self._clock(),
            "ttl": self.ttl_for(kind),
            "source": source,
            "kind": kind,
            "key": key,
            "payload": payload,
        }
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(entry, handle, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)

    def age_seconds(self, source: str, kind: str, key: str) -> float | None:
        """Seconds since the entry was stored, or ``None`` if there is no entry.

        Ignores freshness — used by the data-quality report to disclose how old
        a served-from-cache figure is, even when it is within TTL."""
        path = self._path(source, kind, key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                entry = json.load(handle)
            return self._clock() - float(entry["stored_at"])
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            return None
