"""Source allowlist (PRD 9.4).

The allowlist is what stops an agent citing a tip site or a forum post as
evidence for an industry claim. It is pure, deterministic logic over
``sources.yaml``: given a URL, classify it into a credibility tier (1/2/3),
mark it blocked, or leave it un-allowlisted. Blocked and un-allowlisted sources
are discarded before analysis — down-weighting is not enough for a source whose
whole business is unregistered stock tips.

This lives in the data layer because it gates what data is admitted, and it
imports nothing but the config loader — so it is trivially unit-testable.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.config.settings import load_sources


@dataclass(frozen=True)
class SourceClass:
    url: str
    tier: int | None      # 1/2/3, or None if not on the allowlist
    blocked: bool

    @property
    def allowed(self) -> bool:
        return self.tier is not None and not self.blocked


class SourceAllowlist:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_sources()
        tiers = self.config.get("tiers") or {}
        self._tier_domains: dict[int, list[str]] = {
            int(tier): [d.lower() for d in (spec.get("domains") or [])]
            for tier, spec in tiers.items()
        }
        self._blocked: list[str] = [p.lower() for p in ((self.config.get("blocked") or {}).get("patterns") or [])]

    @staticmethod
    def domain_of(url: str) -> str:
        netloc = urlparse(url if "://" in url else f"http://{url}").netloc.lower()
        netloc = netloc.split("@")[-1].split(":")[0]   # strip creds and port
        return netloc[4:] if netloc.startswith("www.") else netloc

    def is_blocked(self, url: str) -> bool:
        domain = self.domain_of(url)
        full = url.lower()
        for pattern in self._blocked:
            if (fnmatch.fnmatch(domain, pattern) or fnmatch.fnmatch(full, pattern)
                    or (pattern.strip("*") and pattern.strip("*") in domain)):
                return True
        return False

    def tier_of(self, url: str) -> int | None:
        domain = self.domain_of(url)
        for tier in sorted(self._tier_domains):
            for allowed in self._tier_domains[tier]:
                if domain == allowed or domain.endswith("." + allowed):
                    return tier
        return None

    def classify(self, url: str) -> SourceClass:
        if self.is_blocked(url):
            return SourceClass(url, None, True)
        return SourceClass(url, self.tier_of(url), False)

    def is_allowed(self, url: str) -> bool:
        return self.classify(url).allowed

    def credibility(self, tier: int | None) -> float:
        if tier is None:
            return 0.0
        tiers = self.config.get("tiers") or {}
        spec = tiers.get(tier) or tiers.get(str(tier)) or {}
        return float(spec.get("credibility_weight", 0.25))
