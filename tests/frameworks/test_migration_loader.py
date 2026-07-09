from __future__ import annotations

import sys

import pytest

from agentkit.frameworks.migration import load_entry_object


def test_loads_module_entry_and_adds_project_root_for_local_imports(tmp_path):
    (tmp_path / "support.py").write_text("VALUE = 'loaded'\n", encoding="utf8")
    (tmp_path / "entry_loader_mod.py").write_text(
        "from support import VALUE\n\n"
        "class Agent:\n"
        "    value = VALUE\n\n"
        "agent = Agent()\n",
        encoding="utf8",
    )

    try:
        agent = load_entry_object(
            file="entry_loader_mod.py",
            module="entry_loader_mod",
            object_path="agent",
            project_root=".",
            base_dir=tmp_path,
        )
        assert agent.value == "loaded"
    finally:
        sys.modules.pop("entry_loader_mod", None)
        sys.modules.pop("support", None)


def test_loads_file_entry_and_registers_module_for_dataclass_decorators(tmp_path):
    entry = tmp_path / "file_entry.py"
    entry.write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Agent:\n"
        "    name: str = 'file-loaded'\n\n"
        "agent = Agent()\n",
        encoding="utf8",
    )

    try:
        agent = load_entry_object(
            file="file_entry.py",
            module=None,
            object_path="agent",
            base_dir=tmp_path,
            import_name="agentkit_test_file_entry",
        )

        assert agent.name == "file-loaded"
        assert sys.modules["agentkit_test_file_entry"].agent is agent
    finally:
        sys.modules.pop("agentkit_test_file_entry", None)


def test_loads_nested_output_entry_using_project_root(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / "entry_loader_nested_agent.py").write_text(
        "value = 'nested'\n",
        encoding="utf8",
    )
    (tmp_path / "entry_loader_nested_main.py").write_text(
        "from entry_loader_nested_agent import value\n",
        encoding="utf8",
    )

    try:
        value = load_entry_object(
            file="../entry_loader_nested_main.py",
            module="entry_loader_nested_main",
            object_path="value",
            project_root="..",
            base_dir=runtime,
        )
        assert value == "nested"
    finally:
        sys.modules.pop("entry_loader_nested_main", None)
        sys.modules.pop("entry_loader_nested_agent", None)


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Entry file does not exist"):
        load_entry_object(
            file="missing.py",
            module=None,
            object_path="agent",
            base_dir=tmp_path,
        )


def test_missing_object_attribute_raises_clear_error(tmp_path):
    (tmp_path / "entry_loader_attr.py").write_text("agent = object()\n", encoding="utf8")

    try:
        with pytest.raises(AttributeError, match="missing attribute 'missing'"):
            load_entry_object(
                file="entry_loader_attr.py",
                module="entry_loader_attr",
                object_path="agent.missing",
                base_dir=tmp_path,
            )
    finally:
        sys.modules.pop("entry_loader_attr", None)


def test_can_call_zero_argument_factory_entry(tmp_path):
    (tmp_path / "entry_loader_factory.py").write_text(
        "class Agent:\n"
        "    name = 'factory-loaded'\n\n"
        "def build_agent():\n"
        "    return Agent()\n",
        encoding="utf8",
    )

    try:
        agent = load_entry_object(
            file="entry_loader_factory.py",
            module="entry_loader_factory",
            object_path="build_agent",
            base_dir=tmp_path,
            call_factory=True,
        )
        assert agent.name == "factory-loaded"
    finally:
        sys.modules.pop("entry_loader_factory", None)


def test_factory_entry_with_required_arguments_raises_clear_error(tmp_path):
    (tmp_path / "entry_loader_factory_args.py").write_text(
        "def build_agent(settings):\n"
        "    return settings\n",
        encoding="utf8",
    )

    try:
        with pytest.raises(TypeError, match="requires arguments: settings"):
            load_entry_object(
                file="entry_loader_factory_args.py",
                module="entry_loader_factory_args",
                object_path="build_agent",
                base_dir=tmp_path,
                call_factory=True,
            )
    finally:
        sys.modules.pop("entry_loader_factory_args", None)


def test_factory_flag_requires_callable_entry(tmp_path):
    (tmp_path / "entry_loader_not_factory.py").write_text(
        "agent = object()\n",
        encoding="utf8",
    )

    try:
        with pytest.raises(TypeError, match="marked as a factory"):
            load_entry_object(
                file="entry_loader_not_factory.py",
                module="entry_loader_not_factory",
                object_path="agent",
                base_dir=tmp_path,
                call_factory=True,
            )
    finally:
        sys.modules.pop("entry_loader_not_factory", None)
