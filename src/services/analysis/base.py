"""
Shared scaffolding for analysis services.

Today the V1 (FastF1-backed) and V2 (StaticClient-backed) analysis modules
live side-by-side under `v1/` and `v2/`. Each module exposes a `*Plot` and
`*Data` callable. This file is the seam where shared abstractions land when
the V1/V2 pairs are merged into a single source-agnostic service.

Use `cached_or_generate` to wrap MongoDB-cache-then-generate flows so each
analysis stops re-implementing the same try/cache/store dance.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.repositories.plots import get_plot_data_from_mongo


def cached_or_generate(
    year: int,
    identifier: Any,
    session: str,
    data_type: str,
    generator: Callable[[], Any],
    version: str = "v2",
) -> Any:
    """
    Return cached plot data from MongoDB if present, otherwise call `generator`.

    Each analysis module currently inlines this lookup — moving it here lets
    future modules drop ~15 lines of boilerplate.
    """
    cached = get_plot_data_from_mongo(year, identifier, session, data_type, version=version)
    if cached:
        return cached["data"]
    return generator()
