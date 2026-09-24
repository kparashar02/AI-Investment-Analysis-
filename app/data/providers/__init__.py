"""Data providers.

Each provider retrieves raw financial data from one external source and
returns it in the source-agnostic *raw payload* shape documented in
:mod:`app.data.providers.base`. Providers do **no** interpretation and hold
**no** canonical-model knowledge — mapping raw source fields onto the
canonical schema is the sole job of :mod:`app.data.normalise`, so that adding
a second source (FMP) later is a new provider plus a new field map, and
nothing else.
"""

from app.data.providers.base import (
    ProviderDataError,
    ProviderError,
    ProviderUnavailable,
    StatementProvider,
)

__all__ = [
    "StatementProvider",
    "ProviderError",
    "ProviderUnavailable",
    "ProviderDataError",
]
