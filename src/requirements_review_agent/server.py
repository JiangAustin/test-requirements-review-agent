from __future__ import annotations

import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .models import AnalysisSubmission, ProviderMode
from .service import ReviewService

mcp = MCPServer("requirements-review")
_service: ReviewService | None = None


def get_service() -> ReviewService:
    global _service
    if _service is None:
        _service = ReviewService(Path.cwd())
    return _service


@mcp.tool(name="prepare_review", structured_output=True)
def prepare_review(
    pdf_path: str,
    rule_pack: str = "home-iot-v1",
    model_mode: ProviderMode = ProviderMode.COPILOT,
) -> dict[str, object]:
    prepared = get_service().prepare(Path(pdf_path), rule_pack, model_mode)
    return prepared.model_dump(mode="json")


@mcp.tool(name="get_analysis_batch", structured_output=True)
def get_analysis_batch(run_id: str, batch_index: int) -> dict[str, object]:
    batch = get_service().get_batch(run_id, batch_index)
    return batch.model_dump(mode="json")


@mcp.tool(name="submit_analysis", structured_output=True)
def submit_analysis(run_id: str, submission: AnalysisSubmission) -> dict[str, object]:
    status = get_service().submit(run_id, submission)
    return status.model_dump(mode="json")


@mcp.tool(name="run_provider_analysis", structured_output=True)
async def run_provider_analysis(run_id: str) -> dict[str, object]:
    status = await get_service().run_provider(run_id)
    return status.model_dump(mode="json")


@mcp.tool(name="finalize_review", structured_output=True)
def finalize_review(run_id: str) -> dict[str, object]:
    artifacts = get_service().finalize(run_id)
    return artifacts.model_dump(mode="json", by_alias=True)


@mcp.tool(name="get_review_status", structured_output=True)
def get_review_status(run_id: str) -> dict[str, object]:
    status = get_service().status(run_id)
    return status.model_dump(mode="json")


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mcp.run(transport="stdio")


__all__ = [
    "get_analysis_batch",
    "get_review_status",
    "get_service",
    "main",
    "mcp",
    "prepare_review",
    "run_provider_analysis",
    "submit_analysis",
    "finalize_review",
]