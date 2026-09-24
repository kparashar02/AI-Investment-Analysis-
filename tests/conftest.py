"""Shared test fixtures."""

from __future__ import annotations

import pytest

from app.data.fixtures import load_golden
from app.engine.registry import compute_metric_set


@pytest.fixture(scope="session")
def acme():
    """The healthy synthetic company. Every ratio has an exact closed form."""
    return load_golden("acme_industries")


@pytest.fixture(scope="session")
def levcyc():
    """The distressed synthetic company. Exercises the failure branches."""
    return load_golden("leveraged_cyclicals")


@pytest.fixture(scope="session")
def acme_metrics(acme):
    return compute_metric_set(acme.statements, acme.market)


@pytest.fixture(scope="session")
def levcyc_metrics(levcyc):
    return compute_metric_set(levcyc.statements, levcyc.market)
