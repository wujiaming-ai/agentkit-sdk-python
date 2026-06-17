"""AgentKit harness server.

The server builds a long-lived harness agent from environment variables and
serves it at ``POST /harness/invoke``. Agent execution still uses VeADK's Agent
and Runner primitives, but AgentKit-owned integrations, including A2A registry
tools, are imported from ``agentkit.*``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI

from agentkit.apps.harness_app.agent import agent, short_term_memory
from agentkit.apps.harness_app.types import (
    InvokeHarnessRequest,
    InvokeHarnessResponse,
)
from agentkit.apps.harness_app.utils import spawn_harness_agent

from veadk import Agent
from veadk.memory.short_term_memory import ShortTermMemory
from veadk.runner import Runner

logger = logging.getLogger(__name__)

HARNESS_NAME = os.getenv("HARNESS_NAME", "default")


class HarnessApp:
    def __init__(
        self,
        agent: Agent,
        short_term_memory: ShortTermMemory,
        harness_name: str = "default",
    ):
        self.app = FastAPI()
        self.agent = agent
        self.short_term_memory = short_term_memory
        self.harness_name = harness_name
        self.runner = Runner(
            agent=agent,
            short_term_memory=short_term_memory,
            app_name=harness_name,
        )
        self.mount()

    def mount(self):
        @self.app.post("/harness/invoke")
        async def invoke_harness(
            request: InvokeHarnessRequest,
        ) -> InvokeHarnessResponse:
            if request.harness is not None:
                logger.info("Applying one-time harness override: %s", request.harness)
                with tempfile.TemporaryDirectory(prefix="harness_invoke_") as work_dir:
                    one_time_agent = spawn_harness_agent(
                        self.agent, request.harness, download_dir=Path(work_dir)
                    )
                    runner = Runner(
                        agent=one_time_agent,
                        short_term_memory=self.short_term_memory,
                        app_name=self.harness_name,
                    )
                    output = await runner.run(
                        messages=[request.prompt],
                        user_id=request.run_agent_request.user_id,
                        session_id=request.run_agent_request.session_id,
                    )
            else:
                output = await self.runner.run(
                    messages=[request.prompt],
                    user_id=request.run_agent_request.user_id,
                    session_id=request.run_agent_request.session_id,
                )

            return InvokeHarnessResponse(
                harness_name=self.harness_name,
                overwrite=request.harness is not None,
                output=output,
            )

    def serve(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        import uvicorn

        uvicorn.run(self.app, host=host, port=port)


harness_app = HarnessApp(agent, short_term_memory, HARNESS_NAME)
app = harness_app.app


if __name__ == "__main__":
    harness_app.serve(
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8000")),
    )
