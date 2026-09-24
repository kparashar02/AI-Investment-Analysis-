"""Source allowlist classification (app/data/allowlist.py)."""

from __future__ import annotations

import pytest

from app.data.allowlist import SourceAllowlist


@pytest.fixture(scope="module")
def allow():
    return SourceAllowlist()


def test_domain_extraction(allow):
    assert allow.domain_of("https://www.rbi.org.in/scripts/x") == "rbi.org.in"
    assert allow.domain_of("http://economictimes.indiatimes.com:80/a?b=1") == "economictimes.indiatimes.com"
    assert allow.domain_of("moneycontrol.com/news") == "moneycontrol.com"


def test_tier_1_official_and_wires(allow):
    assert allow.classify("https://www.sebi.gov.in/x").tier == 1
    assert allow.classify("https://reuters.com/markets").tier == 1
    assert allow.classify("https://economictimes.indiatimes.com/y").tier == 1


def test_tier_2_industry_bodies(allow):
    assert allow.classify("https://nasscom.in/report").tier == 2
    assert allow.classify("https://www.ibef.org/industry/engineering").tier == 2
    assert allow.classify("https://crisil.com/ratings").tier == 2


def test_subdomain_matches_registered_domain(allow):
    assert allow.classify("https://m.rbi.org.in/notes").tier == 1


def test_blocked_sources_are_not_allowed(allow):
    for url in ["https://twitter.com/someone", "https://x.com/y",
                "https://myblog.blogspot.com/p", "https://hotstocktips.in/buy",
                "https://bestmultibaggerstocks.com/", "https://telegram.org/channel"]:
        c = allow.classify(url)
        assert c.blocked is True
        assert c.allowed is False


def test_unlisted_source_is_neither_tier_nor_blocked(allow):
    c = allow.classify("https://randomunknownsite.example/article")
    assert c.tier is None
    assert c.blocked is False
    assert c.allowed is False        # not on the allowlist -> discarded


def test_credibility_weights_match_tiers(allow):
    assert allow.credibility(1) == 1.00
    assert allow.credibility(2) == 0.60
    assert allow.credibility(3) == 0.25
    assert allow.credibility(None) == 0.0
