"""Launch the Module C API as a real HTTP server for the end-to-end test.

Kept in tests/ on purpose: the test double for the model (RuleLLM) must never be
importable from production code. Module B is *not* mocked here - the test starts
`backend.app` as a second uvicorn process, so this exercises two real servers
over real sockets.
"""
from __future__ import annotations

import os

import uvicorn

from agent.api import create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.service import HarnessService
from tests.module_c.rule_llm import RuleLLM


def main() -> None:
    config = HarnessConfig.from_env()
    service = HarnessService(
        config,
        client=BClient(config),
        trace_dir=os.environ.get("TRACE_DIR", "outputs/module_c/http_trace"),
    )
    application = create_app(
        config=config, service=service, llm=RuleLLM(),
        store_dir=os.environ["STORE_DIR"], dialogue_enabled=True,
    )
    uvicorn.run(application, host="127.0.0.1", port=int(os.environ["PORT"]),
                log_level="warning")


if __name__ == "__main__":
    main()
