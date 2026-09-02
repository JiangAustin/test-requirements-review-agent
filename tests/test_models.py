import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from requirements_review_agent.errors import ReviewError, ReviewException
from requirements_review_agent.models import (
    AnalysisSubmission,
    RequirementAnalysis,
    SourceRef,
)


def valid_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "requirements": [
            {
                "requirement_id": "REQ-a1b2c3d4",
                "checks": [
                    {
                        "rule_id": "behavior.acceptance",
                        "status": "missing",
                        "impact": "both",
                        "severity": "blocking",
                        "finding_type": "suggestion",
                        "evidence": [],
                        "rationale": "未定义成功条件",
                        "question": "成功条件是什么？",
                        "confidence": 0.9,
                    }
                ],
                "scenarios": [
                    {
                        "category": "recovery",
                        "description": "验证 Wi-Fi 断开后的恢复行为",
                        "covered": False,
                        "evidence": [],
                    }
                ],
            }
        ],
    }


def test_analysis_contract_forbids_unknown_and_score_fields() -> None:
    payload = valid_payload()
    payload["requirements"][0]["score"] = 100

    with pytest.raises(ValidationError):
        AnalysisSubmission.model_validate(payload)


def test_source_reference_requires_one_based_page() -> None:
    with pytest.raises(ValidationError):
        SourceRef(page=0, quote="Start")


def test_valid_submission_is_accepted() -> None:
    submission = AnalysisSubmission.model_validate(valid_payload())
    assert submission.requirements[0].requirement_id == "REQ-a1b2c3d4"


def test_submission_rejects_stale_schema_version() -> None:
    payload = valid_payload()
    payload["schema_version"] = "999.0"

    with pytest.raises(ValidationError):
        AnalysisSubmission.model_validate(payload)


def test_fact_finding_requires_evidence() -> None:
    payload = valid_payload()
    payload["requirements"][0]["checks"][0]["finding_type"] = "fact"

    with pytest.raises(ValidationError, match="fact findings must include evidence"):
        AnalysisSubmission.model_validate(payload)


def test_covered_scenario_requires_evidence() -> None:
    payload = valid_payload()
    payload["requirements"][0]["scenarios"][0]["covered"] = True

    with pytest.raises(ValidationError, match="covered scenarios must include evidence"):
        AnalysisSubmission.model_validate(payload)


def test_requirement_analysis_schema_has_no_score() -> None:
    assert "score" not in RequirementAnalysis.model_json_schema()["properties"]


def test_review_exception_includes_code() -> None:
    error = ReviewError(code="ANALYSIS_INVALID", message="分析失败", details={})
    assert "ANALYSIS_INVALID" in str(ReviewException(error))


def test_checked_in_schema_matches_runtime() -> None:
    checked_in = json.loads(
        Path("schemas/analysis-submission.schema.json").read_text(encoding="utf-8")
    )
    assert checked_in == AnalysisSubmission.model_json_schema()
