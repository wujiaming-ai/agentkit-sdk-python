# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline unit guards for ``agent_server_app`` light logic.

These cover only the cheap, side-effect-free surface of
``agentkit.apps.agent_server_app.agent_server_app``:

* ``AgentKitAgentLoader`` -- the tiny loader that wraps a single agent/app and
  answers ``load_agent`` / ``list_agents`` / ``list_agents_detailed``.
* ``AgentkitAgentServerApp.__init__`` argument-validation guards that raise
  *before* any heavy ADK ``AdkWebServer`` / A2A construction happens.

Nothing here constructs a real web server, binds a socket, or calls ``.run()``.
"""

from __future__ import annotations

import pytest
from google.adk.agents.base_agent import BaseAgent
from google.adk.apps.app import App

from agentkit.apps.agent_server_app.agent_server_app import (
    AgentKitAgentLoader,
    AgentkitAgentServerApp,
)


# ---------------------------------------------------------------------------
# Hand-rolled fakes
# ---------------------------------------------------------------------------
class _FakeAgent:
    """Minimal stand-in for a ``BaseAgent``.

    A plain object is deliberately *not* an ``App`` instance, so passing it to
    ``AgentKitAgentLoader`` exercises the ``else`` (BaseAgent) branch of the
    ``isinstance(agent_or_app, App)`` check without dragging in real ADK
    machinery.
    """

    def __init__(self, name: str, description: str | None = None) -> None:
        self.name = name
        self.description = description


def _make_real_app(app_name: str, root_name: str, root_desc: str) -> App:
    """Build a genuine ``App`` wrapping a genuine (empty) ``BaseAgent``.

    ``App`` validates that ``root_agent`` is a real ``BaseAgent``/``BaseNode``
    and that its ``name`` is a valid identifier, so we cannot cheaply fake this
    branch -- a real (but otherwise inert) ``BaseAgent`` is the minimal object
    that passes ``isinstance(agent_or_app, App)``.
    """
    root = BaseAgent(name=root_name, description=root_desc)
    return App(name=app_name, root_agent=root)


# ===========================================================================
# AgentKitAgentLoader -- BaseAgent branch (agent_server_app.py:73-75)
# ===========================================================================
def test_loader_with_bare_agent_uses_agent_as_root_and_app_name():
    agent = _FakeAgent(name="my_agent", description="a fake agent")
    loader = AgentKitAgentLoader(agent)

    # else-branch: root_agent is the agent itself; app_name is agent.name.
    assert loader.root_agent is agent
    assert loader.app_name == "my_agent"
    assert loader.agent_or_app is agent


def test_loader_load_agent_returns_wrapped_entry_on_name_match():
    agent = _FakeAgent(name="my_agent")
    loader = AgentKitAgentLoader(agent)

    assert loader.load_agent("my_agent") is agent


def test_loader_load_agent_raises_value_error_on_unknown_name():
    agent = _FakeAgent(name="my_agent")
    loader = AgentKitAgentLoader(agent)

    with pytest.raises(ValueError) as exc_info:
        loader.load_agent("someone_else")

    # Message names both the unknown request and the one known agent.
    message = str(exc_info.value)
    assert "someone_else" in message
    assert "my_agent" in message


def test_loader_list_agents_returns_single_app_name():
    agent = _FakeAgent(name="my_agent")
    loader = AgentKitAgentLoader(agent)

    assert loader.list_agents() == ["my_agent"]


def test_loader_list_agents_detailed_returns_python_metadata_dict():
    agent = _FakeAgent(name="my_agent", description="a fake agent")
    loader = AgentKitAgentLoader(agent)

    assert loader.list_agents_detailed() == [
        {
            "name": "my_agent",
            "root_agent_name": "my_agent",
            "description": "a fake agent",
            "language": "python",
        }
    ]


def test_loader_list_agents_detailed_coerces_missing_description_to_empty_string():
    # description=None -> the ``or ""`` guard normalizes to the empty string.
    agent = _FakeAgent(name="my_agent", description=None)
    loader = AgentKitAgentLoader(agent)

    detailed = loader.list_agents_detailed()
    assert detailed[0]["description"] == ""


def test_loader_list_agents_detailed_coerces_absent_description_attr_to_empty_string():
    # getattr(..., "description", "") fallback when the attribute is missing.
    class _NoDescriptionAgent:
        name = "bare"

    loader = AgentKitAgentLoader(_NoDescriptionAgent())

    detailed = loader.list_agents_detailed()
    assert detailed[0]["description"] == ""
    assert detailed[0]["root_agent_name"] == "bare"


# ===========================================================================
# AgentKitAgentLoader -- App branch (agent_server_app.py:70-72)
# ===========================================================================
def test_loader_with_app_uses_app_root_agent_and_app_name():
    app = _make_real_app(
        app_name="my_app", root_name="root_agent_x", root_desc="root description"
    )
    loader = AgentKitAgentLoader(app)

    # App-branch: root_agent comes from app.root_agent; app_name from app.name.
    assert loader.root_agent is app.root_agent
    assert loader.root_agent.name == "root_agent_x"
    assert loader.app_name == "my_app"


def test_loader_with_app_load_agent_matches_on_app_name():
    app = _make_real_app(
        app_name="my_app", root_name="root_agent_x", root_desc="root description"
    )
    loader = AgentKitAgentLoader(app)

    assert loader.load_agent("my_app") is app
    with pytest.raises(ValueError):
        # The root agent's name is NOT a valid load key -- only the app name is.
        loader.load_agent("root_agent_x")


def test_loader_with_app_list_agents_detailed_reports_app_and_root_names():
    app = _make_real_app(
        app_name="my_app", root_name="root_agent_x", root_desc="root description"
    )
    loader = AgentKitAgentLoader(app)

    assert loader.list_agents() == ["my_app"]
    assert loader.list_agents_detailed() == [
        {
            "name": "my_app",
            "root_agent_name": "root_agent_x",
            "description": "root description",
            "language": "python",
        }
    ]


# NOTE: The ``agent_or_app.name or self.root_agent.name`` fallback on
# agent_server_app.py:72 (App with a falsy name -> use root_agent.name) is not
# reachable as a pure unit test: ``App`` construction is rejected by pydantic
# validation for any empty/falsy name ("must start with a letter ..."), so a
# falsy-named App instance cannot be built to feed the loader. Branch skipped.


# ===========================================================================
# AgentkitAgentServerApp.__init__ -- early-return validation guards
# (agent_server_app.py:115-123). Each guard raises before any AdkWebServer /
# A2A construction, so plain truthy sentinels are enough to trip them.
# ===========================================================================
def test_server_app_ctor_raises_type_error_when_short_term_memory_is_none():
    with pytest.raises(TypeError) as exc_info:
        AgentkitAgentServerApp(agent=object(), short_term_memory=None)

    assert "short_term_memory is required" in str(exc_info.value)


def test_server_app_ctor_raises_type_error_when_both_agent_and_app_provided():
    # Mutual-exclusion guard fires before any heavy build; sentinels suffice.
    with pytest.raises(TypeError) as exc_info:
        AgentkitAgentServerApp(
            agent=object(),
            short_term_memory=object(),
            app=object(),
        )

    assert "Only one of 'agent' or 'app'" in str(exc_info.value)


def test_server_app_ctor_raises_type_error_when_neither_agent_nor_app_provided():
    # Neither-provided guard fires before any heavy build; sentinel STM only.
    with pytest.raises(TypeError) as exc_info:
        AgentkitAgentServerApp(
            agent=None,
            short_term_memory=object(),
            app=None,
        )

    assert "Either 'agent' or 'app'" in str(exc_info.value)
