from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from requirements_review_agent.models import AnalysisSubmission, ProviderMode
from requirements_review_agent.server import get_service, main, mcp


class StubService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def prepare(self, pdf: Path, rule_pack: str, mode: ProviderMode) -> Any:
        self.calls.append(("prepare", (pdf, rule_pack, mode), {}))
        return type(
            "Prepared",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "run_id": "run-1",
                    "provider_mode": "copilot",
                    "data_destination": "GitHub Copilot model selected in VS Code",
                    "requirement_count": 1,
                    "warnings": [],
                    "batch_count": 1,
                }
            },
        )()

    def get_batch(self, run_id: str, batch_index: int) -> Any:
        self.calls.append(("get_batch", (run_id, batch_index), {}))
        return type(
            "Batch",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "run_id": run_id,
                    "batch_index": batch_index,
                    "requirements": [],
                    "applicable": {},
                    "instructions": "请逐条分析",
                    "analysis_submission_schema": AnalysisSubmission.model_json_schema(),
                }
            },
        )()

    def submit(self, run_id: str, submission: AnalysisSubmission) -> Any:
        self.calls.append(("submit", (run_id, submission), {}))
        return type(
            "Status",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "run_id": run_id,
                    "stage": "analyzed",
                    "requirement_count": 1,
                    "analyzed_count": 1,
                    "warnings": [],
                    "artifacts": None,
                }
            },
        )()

    async def run_provider(self, run_id: str) -> Any:
        self.calls.append(("run_provider", (run_id,), {}))
        return type(
            "Status",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "run_id": run_id,
                    "stage": "analyzed",
                    "requirement_count": 1,
                    "analyzed_count": 1,
                    "warnings": [],
                    "artifacts": None,
                }
            },
        )()

    def finalize(self, run_id: str) -> Any:
        self.calls.append(("finalize", (run_id,), {}))
        return type(
            "Artifacts",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "json": "workspace/.runs/run-1/reports/review.json",
                    "markdown": "workspace/.runs/run-1/reports/review.md",
                    "docx": "workspace/.runs/run-1/reports/review.docx",
                    "status": "complete",
                }
            },
        )()

    def status(self, run_id: str) -> Any:
        self.calls.append(("status", (run_id,), {}))
        return type(
            "Status",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "run_id": run_id,
                    "stage": "prepared",
                    "requirement_count": 1,
                    "analyzed_count": 0,
                    "warnings": ["REQ-1"],
                    "artifacts": None,
                }
            },
        )()


@pytest.mark.asyncio
async def test_server_lists_exactly_six_tools() -> None:
    async with Client(mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert names == {
        "prepare_review",
        "get_analysis_batch",
        "submit_analysis",
        "run_provider_analysis",
        "finalize_review",
        "get_review_status",
    }


@pytest.mark.asyncio
async def test_server_tools_have_nonempty_schemas_and_forward_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubService()
    monkeypatch.setattr("requirements_review_agent.server.get_service", lambda: stub)

    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
        for tool in tools:
            assert tool.input_schema

        prepared = await client.call_tool(
            "prepare_review",
            {
                "pdf_path": str(Path("workspace") / "input.pdf"),
                "rule_pack": "home-iot-v1",
                "model_mode": "copilot",
            },
        )
        status = await client.call_tool("get_review_status", {"run_id": "run-1"})
        submitted = await client.call_tool(
            "submit_analysis",
            {
                "run_id": "run-1",
                "submission": {"schema_version": "1.0", "requirements": []},
            },
        )
        provider_status = await client.call_tool(
            "run_provider_analysis",
            {"run_id": "run-1"},
        )

    assert prepared.structured_content["run_id"] == "run-1"
    assert status.structured_content["stage"] == "prepared"
    assert submitted.structured_content["stage"] == "analyzed"
    assert provider_status.structured_content["stage"] == "analyzed"
    assert [call[0] for call in stub.calls] == ["prepare", "status", "submit", "run_provider"]


def test_main_uses_stderr_logging_and_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_basic_config(*, stream: Any, level: int) -> None:
        captured["stream"] = stream
        captured["level"] = level

    def fake_run(*, transport: str) -> None:
        captured["transport"] = transport

    def fail_print(*args: object, **kwargs: object) -> None:
        raise AssertionError("main must not print")

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    monkeypatch.setattr("requirements_review_agent.server.mcp.run", fake_run)
    monkeypatch.setattr("builtins.print", fail_print)

    main()

    assert captured == {"stream": sys.stderr, "level": logging.INFO, "transport": "stdio"}


def test_get_service_uses_current_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Path("C:/workspace")
    monkeypatch.setattr("requirements_review_agent.server.Path.cwd", lambda: workspace)
    monkeypatch.setattr("requirements_review_agent.server._service", None)

    service = get_service()

    assert service.workspace == workspace