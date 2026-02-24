from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlencode

from agentkit.platform.configuration import VolcConfiguration
from agentkit.platform.provider import CloudProvider


def agentkit_enable_services_url(
    *,
    region: Optional[str] = None,
    project_name: Optional[str] = None,
    platform_config: Optional[VolcConfiguration] = None,
) -> str:
    if platform_config is not None and region is not None:
        raise ValueError("Only one of 'region' or 'platform_config' can be provided.")
    cfg = platform_config or VolcConfiguration(region=region)
    ep = cfg.get_service_endpoint("agentkit")

    base = (
        "https://console.byteplus.com"
        if cfg.provider == CloudProvider.BYTEPLUS
        else "https://console.volcengine.com"
    )

    path = f"/agentkit/region:agentkit+{ep.region}/auth"

    if cfg.provider != CloudProvider.BYTEPLUS:
        return f"{base}{path}"

    project = project_name or os.getenv("AGENTKIT_CONSOLE_PROJECT_NAME") or "default"
    query = urlencode({"projectName": project})
    return f"{base}{path}?{query}"
