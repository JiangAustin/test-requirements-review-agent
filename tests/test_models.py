from pydantic import ValidationError
import pytest

from requirements_review_agent.models import (
    AnalysisSubmission,
    CheckResult,
    CheckStatus,
    Impact,
    RequirementAnalysis,
    Severity,
    SourceRef,
)


def test_analysis_contract_forbids_unknown_and_score_fields() -> None:
    payload = {
        "schema_version": "1.0",
        "requirements": [{
            "requirement_id": "REQ-a1b2c3d4",
            "checks": [{
                "rule_id": "behavior.acceptance",
                "status": "missing",
                "impact": "both",
                "severity": "blocking",
                "finding_type": "suggestion",
                "evidence": [],
                "rationale": "未定义成功条件",
                "question": "成功条件是什么？",
                "confidence": 0.9,
            }],
            "scenarios": [],
            "score": 100,
        }],
    }
    with pytest.raises(ValidationError):
        AnalysisSubmission.model_validate(payload)


def test_source_reference_requires_one_based_page() -> None:
    with pytest.raises(ValidationError):
        SourceRef(page=0, quote="Start")
