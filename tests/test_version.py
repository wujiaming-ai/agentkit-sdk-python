from __future__ import annotations

import tomllib
from pathlib import Path

from agentkit.version import VERSION


def test_version_constant_matches_package_metadata() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert VERSION == pyproject["project"]["version"]


def test_a2a_sdk_dependency_stays_on_adk_compatible_major() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert "a2a-sdk>=0.3.7,<0.4" in pyproject["project"]["dependencies"]
