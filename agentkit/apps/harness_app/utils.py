"""Helpers for assembling the AgentKit harness agent."""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import frontmatter
import httpx
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from agentkit.a2a.registry_client import AgentKitA2ARegistryConfig
from agentkit.apps.harness_app.types import HarnessConfig, HarnessOverrides
from agentkit.tools.builtin_tools.a2a_registry import build_a2a_registry_tools

from veadk import Agent
from veadk.knowledgebase import KnowledgeBase
from veadk.memory.long_term_memory import LongTermMemory
from veadk.memory.short_term_memory import ShortTermMemory
from veadk.tools import get_builtin_tool

logger = logging.getLogger(__name__)

__all__ = [
    "HarnessConfig",
    "HarnessOverrides",
    "split_csv",
    "build_skill_toolset",
    "config_from_env",
    "init_harness_agent",
    "spawn_harness_agent",
]

SKILL_HUB_DOWNLOAD_URL = os.getenv(
    "SKILL_HUB_DOWNLOAD_URL", "https://skills.volces.com/v1/skills/download"
)

_ENV_FIELDS = {
    "model_name": "MODEL_NAME",
    "tools": "TOOLS",
    "skills": "SKILLS",
    "system_prompt": "SYSTEM_PROMPT",
    "runtime": "RUNTIME",
    "structured_tool_calls": "STRUCTURED_TOOL_CALLS",
    "include_tools_every_turn": "INCLUDE_TOOLS_EVERY_TURN",
    "name": "HARNESS_NAME",
    "knowledgebase_type": "KNOWLEDGEBASE_TYPE",
    "longterm_memory_type": "LONG_TERM_MEMORY_TYPE",
    "shortterm_memory_type": "SHORT_TERM_MEMORY_TYPE",
    "registry_type": "REGISTRY_TYPE",
    "registry_space_id": "REGISTRY_SPACE_ID",
    "registry_endpoint": "REGISTRY_ENDPOINT",
    "registry_version": "REGISTRY_VERSION",
    "registry_service_name": "REGISTRY_SERVICE_NAME",
    "registry_region": "REGISTRY_REGION",
    "registry_top_k": "REGISTRY_TOP_K",
    "registry_timeout_ms": "REGISTRY_TIMEOUT_MS",
    "registry_poll_interval_ms": "REGISTRY_POLL_INTERVAL_MS",
}


def split_csv(value: str) -> list[str]:
    """Split a comma-separated string into trimmed, non-empty names."""

    return [item.strip() for item in value.split(",") if item.strip()]


def _download_and_extract_skill(skill: str, dest_dir: Path) -> Path:
    name = skill.strip("/")
    url = f"{SKILL_HUB_DOWNLOAD_URL.rstrip('/')}/{name}"
    logger.info("Downloading skill %r from %s", skill, url)

    response = httpx.get(url, timeout=60, follow_redirects=True)
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to download skill '{skill}': HTTP {response.status_code}"
        )

    staging = dest_dir / f"{name.split('/')[-1]}__staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staging_root = staging.resolve()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        for member in zf.namelist():
            if not (staging / member).resolve().is_relative_to(staging_root):
                raise RuntimeError(f"Unsafe path in skill '{skill}' zip: {member}")
        zf.extractall(staging)

    skill_md = staging / "SKILL.md"
    if not skill_md.exists():
        skill_md = staging / "skill.md"
    if not skill_md.exists():
        raise RuntimeError(f"Skill '{skill}' has no SKILL.md")
    declared_name = frontmatter.loads(
        skill_md.read_text(encoding="utf-8")
    ).metadata.get("name")
    if not declared_name:
        raise RuntimeError(f"Skill '{skill}' SKILL.md has no 'name' in frontmatter")

    skill_dir = dest_dir / str(declared_name)
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    staging.rename(skill_dir)
    logger.info("Extracted skill %r (name=%r) to %s", skill, declared_name, skill_dir)
    return skill_dir


def build_skill_toolset(
    skills: list[str], download_dir: Path | None = None
) -> SkillToolset | None:
    """Download each skill from the hub and load them as a single ADK toolset."""

    if download_dir is None:
        download_dir = Path(tempfile.mkdtemp(prefix="harness_skills_"))
    loaded_skills = []
    for skill in skills:
        try:
            loaded_skills.append(
                load_skill_from_dir(_download_and_extract_skill(skill, download_dir))
            )
        except Exception as exc:
            logger.warning("Skipping skill %r: %s", skill, exc)

    if not loaded_skills:
        logger.warning("No skills loaded successfully; skipping skill toolset.")
        return None
    return SkillToolset(skills=loaded_skills)


def config_from_env() -> HarnessConfig:
    """Parse the environment into a :class:`HarnessConfig`."""

    kwargs: dict[str, Any] = {
        field: os.environ[env]
        for field, env in _ENV_FIELDS.items()
        if env in os.environ
    }
    return HarnessConfig(**kwargs)


def _assemble_agent(config: HarnessConfig) -> tuple[Agent, ShortTermMemory]:
    tools = [get_builtin_tool(name) for name in split_csv(config.tools)]

    skills = split_csv(config.skills)
    if skills:
        logger.info("Loading skills %s for harness.", skills)
        skill_toolset = build_skill_toolset(skills)
        if skill_toolset is not None:
            tools.append(skill_toolset)

    if config.registry_type:
        logger.info(
            "Mounting AgentKit A2A registry tools: type=%s", config.registry_type
        )
        tools.extend(
            build_a2a_registry_tools(
                AgentKitA2ARegistryConfig(
                    space_id=config.registry_space_id,
                    endpoint=config.registry_endpoint,
                    version=config.registry_version,
                    service_name=config.registry_service_name,
                    region=config.registry_region,
                    top_k=config.registry_top_k,
                    timeout_ms=config.registry_timeout_ms,
                    poll_interval_ms=config.registry_poll_interval_ms,
                )
            )
        )

    knowledgebase = None
    if config.knowledgebase_type:
        logger.info(
            "Initializing knowledge base: backend=%s index=%s",
            config.knowledgebase_type,
            config.app_name,
        )
        knowledgebase = KnowledgeBase(
            backend=config.knowledgebase_type,  # type: ignore[arg-type]
            app_name=config.app_name,
        )

    long_term_memory = None
    if config.longterm_memory_type:
        logger.info(
            "Initializing long-term memory: backend=%s index=%s",
            config.longterm_memory_type,
            config.app_name,
        )
        long_term_memory = LongTermMemory(
            backend=config.longterm_memory_type,  # type: ignore[arg-type]
            app_name=config.app_name,
        )

    logger.info("Initializing short-term memory: backend=%s", config.shortterm_memory_type)
    short_term_memory = ShortTermMemory(
        backend=config.shortterm_memory_type  # type: ignore[arg-type]
    )

    agent = Agent(
        name="harness_agent",
        model_name=config.model_name,
        instruction=config.system_prompt,
        tools=tools,
        runtime=config.runtime,
        enable_responses=config.structured_tool_calls,
        enable_responses_cache=not config.include_tools_every_turn,
        knowledgebase=knowledgebase,
        long_term_memory=long_term_memory,
        short_term_memory=short_term_memory,
    )
    return agent, short_term_memory


def init_harness_agent() -> tuple[Agent, ShortTermMemory]:
    return _assemble_agent(config_from_env())


def _tool_name(tool: Any) -> str | None:
    return getattr(tool, "__name__", None) or getattr(tool, "name", None)


def _add_incremental_tools(agent: Agent, tool_names: list[str]) -> None:
    existing = {name for tool in agent.tools if (name := _tool_name(tool))}
    for name in tool_names:
        if name in existing:
            logger.info("Tool %r already on the agent; skipping.", name)
            continue
        agent.tools.append(get_builtin_tool(name))
        existing.add(name)


def _add_incremental_skills(
    agent: Agent, skill_ids: list[str], download_dir: Path | None = None
) -> None:
    toolset = build_skill_toolset(skill_ids, download_dir=download_dir)
    if toolset is None:
        return
    new_skills = toolset._list_skills()

    existing_toolset = next(
        (tool for tool in agent.tools if isinstance(tool, SkillToolset)), None
    )
    if existing_toolset is None:
        agent.tools.append(toolset)
        return

    existing_skills = existing_toolset._list_skills()
    existing_names = {skill.name for skill in existing_skills}
    new_skills = [skill for skill in new_skills if skill.name not in existing_names]
    if not new_skills:
        logger.info("All requested skills already loaded; skipping.")
        return

    agent.tools.remove(existing_toolset)
    agent.tools.append(SkillToolset(skills=existing_skills + new_skills))


def spawn_harness_agent(
    base_agent: Agent, overrides: HarnessOverrides, download_dir: Path | None = None
) -> Agent:
    """Clone the base agent for a one-off invocation and apply overrides."""

    set_fields = overrides.model_fields_set

    update: dict[str, Any] = {}
    if "system_prompt" in set_fields:
        update["instruction"] = overrides.system_prompt
    if "runtime" in set_fields:
        update["runtime"] = overrides.runtime
    cloned = base_agent.clone(update=update)

    if "model_name" in set_fields:
        cloned.update_model(overrides.model_name)

    if "tools" in set_fields:
        _add_incremental_tools(cloned, split_csv(overrides.tools))

    if "skills" in set_fields:
        _add_incremental_skills(cloned, split_csv(overrides.skills), download_dir)

    return cloned
