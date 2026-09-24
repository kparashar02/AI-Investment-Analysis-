"""Data layer — Layer 1 of the architecture (PRD 7).

Providers retrieve raw source data; :mod:`app.data.normalise` maps it onto the
canonical statement schema; :mod:`app.data.cache` and
:mod:`app.data.rate_limiter` keep the system within free-tier API limits and
reproducible; :class:`app.data.service.DataService` ties them together behind
one entry point that hands the engine canonical models.

This layer may touch the network. The engine may not — see
``tests/unit/test_layering.py``.
"""
