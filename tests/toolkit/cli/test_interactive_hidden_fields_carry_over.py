from __future__ import annotations

import pytest


def test_generate_config_rejects_non_dict_carry_over_config(monkeypatch) -> None:
    from agentkit.toolkit.cli.interactive_config import AutoPromptGenerator
    from agentkit.toolkit.config import CommonConfig

    monkeypatch.setattr(
        AutoPromptGenerator, "_show_welcome_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator, "_show_completion_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator,
        "_prompt_for_field",
        lambda self,
        name,
        field_type,
        description,
        default,
        metadata=None,
        current=1,
        total=1,
        current_config=None,
        resolver_context=None: default,
    )

    generator = AutoPromptGenerator()

    with pytest.raises(TypeError, match=r"carry_over_config must be a dict"):
        generator.generate_config(CommonConfig, {}, carry_over_config=[])  # type: ignore[arg-type]


def test_generate_config_does_not_carry_hidden_fields_from_prefill_when_carry_over_missing(
    monkeypatch,
) -> None:
    from agentkit.toolkit.cli.interactive_config import AutoPromptGenerator
    from agentkit.toolkit.config import CommonConfig

    monkeypatch.setattr(
        AutoPromptGenerator, "_show_welcome_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator, "_show_completion_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator,
        "_prompt_for_field",
        lambda self,
        name,
        field_type,
        description,
        default,
        metadata=None,
        current=1,
        total=1,
        current_config=None,
        resolver_context=None: default,
    )

    generator = AutoPromptGenerator()

    prefill = CommonConfig.from_dict({}).to_dict()
    carry_over = {
        "agent_name": "demo",
        "entry_point": "agent.py",
        "launch_type": "cloud",
    }

    result = generator.generate_config(
        CommonConfig,
        prefill,
        carry_over_config=carry_over,
    )

    assert "cloud_provider" not in result


def test_generate_config_carries_hidden_fields_from_carry_over(monkeypatch) -> None:
    from agentkit.toolkit.cli.interactive_config import AutoPromptGenerator
    from agentkit.toolkit.config import CommonConfig

    monkeypatch.setattr(
        AutoPromptGenerator, "_show_welcome_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator, "_show_completion_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator,
        "_prompt_for_field",
        lambda self,
        name,
        field_type,
        description,
        default,
        metadata=None,
        current=1,
        total=1,
        current_config=None,
        resolver_context=None: default,
    )

    generator = AutoPromptGenerator()

    prefill = CommonConfig.from_dict({}).to_dict()
    carry_over = {
        "agent_name": "demo",
        "entry_point": "agent.py",
        "launch_type": "cloud",
        "cloud_provider": "byteplus",
    }

    result = generator.generate_config(
        CommonConfig,
        prefill,
        carry_over_config=carry_over,
    )

    assert result["cloud_provider"] == "byteplus"


def test_generate_config_default_behavior_carries_hidden_fields_from_existing_config(
    monkeypatch,
) -> None:
    from agentkit.toolkit.cli.interactive_config import AutoPromptGenerator
    from agentkit.toolkit.config import CommonConfig

    monkeypatch.setattr(
        AutoPromptGenerator, "_show_welcome_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator, "_show_completion_panel", lambda *a, **k: None
    )
    monkeypatch.setattr(
        AutoPromptGenerator,
        "_prompt_for_field",
        lambda self,
        name,
        field_type,
        description,
        default,
        metadata=None,
        current=1,
        total=1,
        current_config=None,
        resolver_context=None: default,
    )

    generator = AutoPromptGenerator()

    existing_config = {
        "agent_name": "demo",
        "entry_point": "agent.py",
        "launch_type": "cloud",
        "cloud_provider": "byteplus",
    }

    result = generator.generate_config(CommonConfig, existing_config)

    assert result["cloud_provider"] == "byteplus"
