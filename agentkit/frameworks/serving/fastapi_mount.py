"""Helpers for mounting an existing FastAPI app beside AgentKit routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        raise ValueError("legacy FastAPI mount prefix must not be empty.")
    if not prefix.startswith("/"):
        raise ValueError("legacy FastAPI mount prefix must start with '/'.")
    if prefix != "/" and prefix.endswith("/"):
        return prefix.rstrip("/")
    return prefix


def mount_legacy_fastapi_app(
    app: FastAPI,
    legacy_app: Any,
    *,
    prefix: str = "/legacy",
    allow_root: bool = False,
) -> None:
    """Mount a user-owned FastAPI app without rewriting its routes."""

    normalized_prefix = _normalize_prefix(prefix)
    if normalized_prefix == "/" and not allow_root:
        raise ValueError(
            "mounting a legacy FastAPI app at '/' can shadow AgentKit routes; "
            "pass allow_root=True only when you intentionally want that behavior."
        )
    if not isinstance(legacy_app, FastAPI):
        raise TypeError("legacy_app must be a fastapi.FastAPI instance.")

    logger.info("Mounting legacy FastAPI app at %s", normalized_prefix)
    app.mount(normalized_prefix, legacy_app)
    _promote_mount(app, normalized_prefix)


def _promote_mount(app: FastAPI, path: str) -> None:
    routes = app.router.routes
    route_index = next(
        (
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == ("" if path == "/" else path)
        ),
        None,
    )
    if route_index is None:
        return

    route = routes.pop(route_index)
    insert_at = len(routes)
    for index, existing in enumerate(routes):
        if getattr(existing, "path", None) in {"", "/"}:
            insert_at = index
            break
    routes.insert(insert_at, route)
