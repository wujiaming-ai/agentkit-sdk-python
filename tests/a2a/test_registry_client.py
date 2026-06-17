"""Tests for the AgentKit A2A registry client and built-in tools."""

from __future__ import annotations

import json
from unittest.mock import patch

from agentkit.a2a.registry_client import (
    AgentKitA2ARegistryConfig,
    RegistryError,
    registry_config_from_env,
    search_agent_cards,
)
from agentkit.tools.builtin_tools.a2a_registry import build_a2a_registry_tools


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"HTTP {self.status_code}", response=self)


def test_registry_config_from_env_reads_harness_fields(monkeypatch):
    monkeypatch.setenv("REGISTRY_SPACE_ID", "space-test")
    monkeypatch.setenv("REGISTRY_TOP_K", "5")
    monkeypatch.setenv("REGISTRY_REGION", "cn-beijing")

    config = registry_config_from_env()

    assert config.space_id == "space-test"
    assert config.top_k == 5
    assert config.region == "cn-beijing"


def test_build_a2a_registry_tools_exposes_mcp_compatible_names():
    tools = build_a2a_registry_tools(AgentKitA2ARegistryConfig(space_id="space-test"))

    assert [tool.__name__ for tool in tools] == [
        "a2a_registry_search_agent_cards",
        "a2a_registry_task_create",
        "a2a_registry_task_poll",
    ]


def test_a2a_registry_tool_descriptions_guide_model_flow():
    search_tool, create_tool, poll_tool = build_a2a_registry_tools(
        AgentKitA2ARegistryConfig(space_id="space-test")
    )

    assert "a2a_registry_task_create" in (search_tool.__doc__ or "")
    assert "a2a_registry_task_poll" in (create_tool.__doc__ or "")
    assert "terminal" in (poll_tool.__doc__ or "")


@patch("agentkit.a2a.registry_client.requests.post")
def test_search_agent_cards_uses_agentkit_openapi_and_sanitizes_response(
    post, monkeypatch
):
    monkeypatch.setenv("AGENTKIT_ACCESS_KEY", "ak")
    monkeypatch.setenv("AGENTKIT_SECRET_KEY", "sk")
    post.return_value = _Response(
        {
            "ResponseMetadata": {"RequestId": "req-1"},
            "Result": {
                "TotalCount": 1,
                "AgentCards": [
                    json.dumps(
                        {
                            "name": "researcher",
                            "description": "Research agent",
                            "url": "https://example.test/a2a",
                            "version": "v1",
                            "skills": [
                                {
                                    "id": "s1",
                                    "name": "search",
                                    "description": "Search web",
                                    "tags": ["web"],
                                }
                            ],
                        }
                    )
                ],
            },
        }
    )

    result = search_agent_cards(
        "find papers",
        config=AgentKitA2ARegistryConfig(space_id="space-test", top_k=3),
    )

    assert result["outcome"] == "success"
    assert result["agents"] == [
        {
            "name": "researcher",
            "description": "Research agent",
            "version": "v1",
            "protocol_version": "",
            "preferred_transport": "",
            "registration_type": "",
            "skills": [
                {
                    "id": "s1",
                    "name": "search",
                    "description": "Search web",
                    "tags": ["web"],
                }
            ],
        }
    ]
    assert post.call_args.kwargs["params"] == {
        "Action": "SearchAgentCards",
        "Version": "2025-10-30",
    }
    assert json.loads(post.call_args.kwargs["data"]) == {
        "SpaceId": "space-test",
        "Prompt": "find papers",
        "TopK": 3,
    }
    assert "https://example.test/a2a" not in json.dumps(result)


def test_tool_returns_structured_failure_without_raising(monkeypatch):
    for name in ["REGISTRY_SPACE_ID", "AGENTKIT_A2A_SPACE_ID", "A2A_REGISTRY_SPACE_ID"]:
        monkeypatch.delenv(name, raising=False)
    tool = build_a2a_registry_tools(AgentKitA2ARegistryConfig())[0]

    result = tool(prompt="find papers")

    assert result["outcome"] == "failure"
    assert result["error_code"] == "CONFIG_MISSING"


def test_missing_prompt_raises_registry_error():
    try:
        search_agent_cards("", config=AgentKitA2ARegistryConfig(space_id="space-test"))
    except RegistryError as exc:
        assert exc.code == "INVALID_ARGUMENT"
    else:
        raise AssertionError("expected RegistryError")
