"""Helpers used by generated framework migration apps."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


def _prepend_sys_path(path: Path) -> None:
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)


def _load_module_from_file(entry_path: Path, import_name: str) -> ModuleType:
    if not entry_path.is_file():
        raise FileNotFoundError(f"Entry file does not exist: {entry_path}")

    spec = importlib.util.spec_from_file_location(import_name, entry_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load entry module from {entry_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(import_name) is module:
            del sys.modules[import_name]
        raise
    return module


def _resolve_object(module: ModuleType, object_path: str) -> Any:
    if not object_path:
        raise ValueError("entry object path is required")

    target: Any = module
    for attr in object_path.split("."):
        if not attr:
            raise ValueError(
                f"entry object path contains an empty attribute: {object_path!r}"
            )
        try:
            target = getattr(target, attr)
        except AttributeError as exc:
            raise AttributeError(
                f"Entry object {object_path!r} was not found; "
                f"missing attribute {attr!r} on {target!r}."
            ) from exc
    return target


def _call_zero_arg_factory(target: Any, object_path: str) -> Any:
    if not callable(target):
        raise TypeError(
            f"Entry object {object_path!r} was marked as a factory, "
            "but the loaded object is not callable."
        )

    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"Entry factory {object_path!r} has no inspectable signature. "
            "Expose a zero-argument factory or a constructed agent object."
        ) from exc

    required_params = [
        name
        for name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty
        and param.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    ]
    if required_params:
        formatted = ", ".join(required_params)
        raise TypeError(
            f"Entry factory {object_path!r} requires arguments: {formatted}. "
            "agentkit migrate can only call zero-argument factories; expose a "
            "thin zero-argument entry object for migration."
        )

    result = target()
    if inspect.isawaitable(result):
        raise TypeError(
            f"Entry factory {object_path!r} returned an awaitable object. "
            "Async factories are not supported by generated migration apps; "
            "expose a synchronous factory or a constructed agent object."
        )
    return result


def load_entry_object(
    *,
    file: str,
    object_path: str,
    module: str | None = None,
    project_root: str | Path = ".",
    base_dir: str | Path | None = None,
    import_name: str = "agentkit_migrated_entry",
    call_factory: bool = False,
) -> Any:
    """Load an object from a migrated project's original entry reference.

    The generated migration app lives beside or below the user's project files.
    This helper keeps that app small while preserving the import behavior that
    users expect from running their original project.
    """

    base_path = (
        Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()
    )
    project_root_path = (base_path / project_root).resolve()
    _prepend_sys_path(project_root_path)

    if module:
        loaded_module = importlib.import_module(module)
    else:
        entry_path = (base_path / file).resolve()
        loaded_module = _load_module_from_file(entry_path, import_name)

    target = _resolve_object(loaded_module, object_path)
    if call_factory:
        return _call_zero_arg_factory(target, object_path)
    return target
