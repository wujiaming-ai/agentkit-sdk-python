"""OpenTelemetry context helpers used by AgentKit instrumentation."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import context as context_api

logger = logging.getLogger(__name__)


def safe_detach_context_token(token: Any) -> bool:
    """Detach an OTel context token without logging on copied Context cleanup.

    Starlette streaming responses can finish in a copied ``contextvars.Context``.
    The public ``opentelemetry.context.detach`` logs an error before returning in
    that case. We use the standard ``contextvars.Token`` API when available so
    cross-context cleanup remains quiet and fail-open.
    """

    token_var = getattr(token, "var", None)
    if token_var is not None:
        try:
            token_var.reset(token)
            return True
        except ValueError:
            return False
        except Exception:
            logger.warning("Failed to detach OpenTelemetry context.", exc_info=True)
            return False

    try:
        context_api.detach(token)
        return True
    except Exception:
        logger.warning("Failed to detach OpenTelemetry context.", exc_info=True)
        return False
