"""Assemble a single harness agent from environment variables."""

from agentkit.apps.harness_app.utils import init_harness_agent

agent, short_term_memory = init_harness_agent()
