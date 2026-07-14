from __future__ import annotations

import sys

import pytest

from agentkit.frameworks import migration as migration_module
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


def test_file_entry_import_failures_do_not_leave_partial_modules(tmp_path):
    (tmp_path / "broken_entry.py").write_text(
        "PARTIAL = True\nraise RuntimeError('import failed')\n",
        encoding="utf8",
    )

    with pytest.raises(RuntimeError, match="import failed"):
        load_entry_object(
            file="broken_entry.py",
            module=None,
            object_path="agent",
            base_dir=tmp_path,
            import_name="agentkit_broken_entry",
        )

    assert "agentkit_broken_entry" not in sys.modules


def test_file_entry_reports_unloadable_module_specs(tmp_path, monkeypatch):
    entry = tmp_path / "entry.py"
    entry.write_text("agent = object()\n", encoding="utf8")
    monkeypatch.setattr(
        migration_module.importlib.util,
        "spec_from_file_location",
        lambda import_name, entry_path: None,
    )

    with pytest.raises(RuntimeError, match="Cannot load entry module"):
        load_entry_object(
            file="entry.py",
            module=None,
            object_path="agent",
            base_dir=tmp_path,
        )


def test_invalid_object_paths_raise_clear_errors(tmp_path):
    (tmp_path / "entry_loader_bad_path.py").write_text("agent = object()\n", encoding="utf8")

    try:
        with pytest.raises(ValueError, match="entry object path is required"):
            load_entry_object(
                file="entry_loader_bad_path.py",
                module="entry_loader_bad_path",
                object_path="",
                base_dir=tmp_path,
            )
        with pytest.raises(ValueError, match="empty attribute"):
            load_entry_object(
                file="entry_loader_bad_path.py",
                module="entry_loader_bad_path",
                object_path="agent.",
                base_dir=tmp_path,
            )
    finally:
        sys.modules.pop("entry_loader_bad_path", None)


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


def test_factory_entry_with_uninspectable_signature_raises_clear_error(tmp_path):
    (tmp_path / "entry_loader_uninspectable.py").write_text(
        "class BuildAgent:\n"
        "    @property\n"
        "    def __signature__(self):\n"
        "        raise ValueError('no signature')\n"
        "    def __call__(self):\n"
        "        return object()\n\n"
        "build_agent = BuildAgent()\n",
        encoding="utf8",
    )

    try:
        with pytest.raises(TypeError, match="has no inspectable signature"):
            load_entry_object(
                file="entry_loader_uninspectable.py",
                module="entry_loader_uninspectable",
                object_path="build_agent",
                base_dir=tmp_path,
                call_factory=True,
            )
    finally:
        sys.modules.pop("entry_loader_uninspectable", None)


def test_async_factory_entry_is_rejected(tmp_path):
    (tmp_path / "entry_loader_async_factory.py").write_text(
        "async def build_agent():\n"
        "    return object()\n",
        encoding="utf8",
    )

    try:
        with pytest.raises(TypeError, match="Async factories are not supported"):
            load_entry_object(
                file="entry_loader_async_factory.py",
                module="entry_loader_async_factory",
                object_path="build_agent",
                base_dir=tmp_path,
                call_factory=True,
            )
    finally:
        sys.modules.pop("entry_loader_async_factory", None)


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
