"""Harness parameter schemas for the AgentKit harness app."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_MODEL_AGENT_NAME = "doubao-seed-1-6-250615"
DEFAULT_INSTRUCTION = "You are a helpful assistant."


class HarnessOverrides(BaseModel):
    """Harness parameters that may be overridden per invocation."""

    model_name: str = Field(
        default=DEFAULT_MODEL_AGENT_NAME, description="Reasoning model name."
    )
    tools: str = Field(
        default="",
        description="Comma-separated built-in tool names, e.g. web_search,web_fetch.",
    )
    skills: str = Field(default="", description="Comma-separated skill hub names.")
    system_prompt: str = Field(
        default=DEFAULT_INSTRUCTION,
        description="System prompt / instruction.",
    )
    runtime: Literal["adk", "codex"] = Field(
        default="adk", description="Agent runtime backend."
    )
    registry_space_id: str = Field(default="")
    registry_endpoint: str = Field(default="")
    registry_region: str = Field(default="")
    registry_top_k: int = Field(default=3)


class HarnessConfig(HarnessOverrides):
    """Full harness parameters fixed when the agent is created."""

    app_name: str = Field(default="harness_app", alias="name")
    system_prompt: str = Field(default=DEFAULT_INSTRUCTION)
    knowledgebase_type: str = Field(default="")
    longterm_memory_type: str = Field(default="")
    shortterm_memory_type: str = Field(default="local")
    runtime: Literal["adk", "codex"] = Field(default="adk")
    structured_tool_calls: bool = Field(default=False)
    include_tools_every_turn: bool = Field(default=True)
    registry_type: Literal["", "agentkit_a2a"] = Field(default="")
    registry_space_id: str = Field(default="")
    registry_endpoint: str = Field(default="")
    registry_version: str = Field(default="")
    registry_service_name: str = Field(default="")
    registry_region: str = Field(default="")
    registry_top_k: int = Field(default=3)
    registry_timeout_ms: int = Field(default=60000)
    registry_poll_interval_ms: int = Field(default=5000)


class RunAgentRequest(BaseModel):
    user_id: str
    session_id: str


class InvokeHarnessRequest(BaseModel):
    prompt: str
    harness_name: str
    harness: HarnessOverrides | None = None
    run_agent_request: RunAgentRequest


class InvokeHarnessResponse(BaseModel):
    harness_name: str
    overwrite: bool = Field(default=False)
    output: str
