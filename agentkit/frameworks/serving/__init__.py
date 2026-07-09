"""Serving compatibility helpers for migrated framework agents."""

from agentkit.frameworks.serving.fastapi_mount import mount_legacy_fastapi_app
from agentkit.frameworks.serving.langserve import attach_langserve_compat_routes

__all__ = ["attach_langserve_compat_routes", "mount_legacy_fastapi_app"]
