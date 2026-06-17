"""Contract tests for the AgentKit harness runtime app."""

from pathlib import Path

from agentkit.toolkit.executors import init_executor


def test_harness_dockerfile_starts_agentkit_harness_app():
    dockerfile = init_executor._HARNESS_DOCKERFILE

    assert "agentkit-sdk-python" in dockerfile
    assert "agentkit.apps.harness_app.app:app" in dockerfile
    assert "veadk.cloud.harness_app.app:app" not in dockerfile


def test_agentkit_harness_app_uses_agentkit_a2a_registry_tools():
    utils_source = Path("agentkit/apps/harness_app/utils.py").read_text()

    assert "from agentkit.a2a.registry_client import AgentKitA2ARegistryConfig" in utils_source
    assert (
        "from agentkit.tools.builtin_tools.a2a_registry import build_a2a_registry_tools"
        in utils_source
    )
    assert "from veadk.a2a.registry_client" not in utils_source
    assert "from veadk.tools.builtin_tools.a2a_registry" not in utils_source
