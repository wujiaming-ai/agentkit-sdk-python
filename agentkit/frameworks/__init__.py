"""Framework adapters for running third-party agents in AgentKit apps."""

from __future__ import annotations

from typing import Any

from agentkit.frameworks._common import (
    FrameworkBridgeError,
    UnsupportedFrameworkAgentError,
)

__all__ = [
    "FrameworkBridgeError",
    "LangChainAgentkitBridge",
    "LangGraphAgentkitBridge",
    "UnsupportedFrameworkAgentError",
]


def __getattr__(name: str) -> Any:
    if name == "LangChainAgentkitBridge":
        from agentkit.frameworks.langchain import LangChainAgentkitBridge

        return LangChainAgentkitBridge
    if name == "LangGraphAgentkitBridge":
        from agentkit.frameworks.langgraph import LangGraphAgentkitBridge

        return LangGraphAgentkitBridge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
