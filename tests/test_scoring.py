from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from requirements_review_agent.analysis import (
    AnalysisBatch,
    build_analysis_batch,
    validate_submission,
)
from requirements_review_agent.errors import ANALYSIS_INVALID, RULE_PACK_INVALID, ReviewException
from requirements_review_agent.models import (
    AnalysisSubmission,
    ApplicableRule,
    AtomicRequirement,
    CheckResult,
    CheckStatus,
    FindingType,
    Impact,
    RequirementAnalysis,
    ScenarioResult,
    Severity,
    SourceRef,
)
from requirements_review_agent.scoring import aggregate_scores, score_requirements


def source(
    page: int,
    quote: str,
    *,
    section: str | None = None,
    table_index: int | None = None,
) -> SourceRef:
    return SourceRef(page=page, quote=quote, section=section, table_index=table_index)


def requirement(
    requirement_id: str = "REQ-1",
    *,
    text: str | None = None,
    sources: tuple[SourceRef, ...] | None = None,
) -> AtomicRequirement:
    return AtomicRequirement(
        requirement_id=requirement_id,
        text=text or f"Requirement {requirement_id}",
        sources=sources
        or (
            source(1, f"Quote for {requirement_id}", section="1.0 Overview"),
            source(2, f"Table quote for {requirement_id}", section="2.0 Table", table_index=0),
        ),
    )


def rule(
    rule_id: str,
    *,
    weight: int = 1,
    scenario_category: str | None = "baseline",
    impact: Impact = Impact.BOTH,
) -> ApplicableRule:
    return ApplicableRule(
        rule_id=rule_id,
        question=f"请确认 {rule_id} 是否满足？",
        weight=weight,
        impact=impact,
        scenario_category=scenario_category,
        always=True,
        keywords=(),
    )


def check(
    rule_id: str,
    status: CheckStatus,
    *,
    evidence: tuple[SourceRef, ...] = (),
    question: str | None = None,
    finding_type: FindingType = FindingType.SUGGESTION,
    confidence: float = 0.8,
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        status=status,
        impact=Impact.BOTH,
        severity=Severity.IMPORTANT,
        finding_type=finding_type,
        evidence=evidence,
        rationale=f"Rationale for {rule_id}",
        question=question,
        confidence=confidence,
    )


def scenario(
    category: str,
    *,
    covered: bool,
    evidence: tuple[SourceRef, ...] = (),
) -> ScenarioResult:
    return ScenarioResult(
        category=category,
        description=f"Scenario {category}",
        covered=covered,
        evidence=evidence,
    )


def analyzed(
    requirement_id: str = "REQ-1",
    *,
    checks: tuple[CheckResult, ...] | None = None,
    scenarios: tuple[ScenarioResult, ...] | None = None,
) -> RequirementAnalysis:
    return RequirementAnalysis(
        requirement_id=requirement_id,
        checks=checks or (),
        scenarios=scenarios or (),
    )


def submission(*analyses: RequirementAnalysis) -> AnalysisSubmission:
    return AnalysisSubmission(schema_version="1.0", requirements=analyses)


def rules_by_requirement(
    **entries: tuple[ApplicableRule, ...],
) -> dict[str, tuple[ApplicableRule, ...]]:
    return dict(entries)


def test_build_analysis_batch_embeds_metadata_rules_and_schema() -> None:
    req = requirement("REQ-batch")
    applicable = (rule("acceptance.criteria", scenario_category="baseline"),)

    batch = build_analysis_batch(
        run_id="run-123",
        batch_index=2,
        requirements=(req,),
        applicable={req.requirement_id: applicable},
    )

    assert isinstance(batch, AnalysisBatch)
    assert batch.run_id == "run-123"
    assert batch.batch_index == 2
    assert batch.requirements == (req,)
    assert batch.applicable[req.requirement_id] == applicable
    assert batch.instructions
    assert '"score"' not in json.dumps(
        batch.analysis_submission_schema,
        ensure_ascii=False,
        sort_keys=True,
    )


def test_validate_submission_rejects_duplicate_requirement_ids() -> None:
    req = requirement("REQ-1")
    analysis = analyzed(req.requirement_id)
    duplicate = AnalysisSubmission(
        schema_version="1.0",
        requirements=(analysis, analyzed(req.requirement_id)),
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(duplicate, (req,), {req.requirement_id: ()})


def test_validate_submission_rejects_missing_requirement_ids() -> None:
    req1 = requirement("REQ-1")
    req2 = requirement("REQ-2")

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analyzed("REQ-1")), (req1, req2), {"REQ-1": (), "REQ-2": ()})


def test_validate_submission_rejects_unknown_requirement_ids() -> None:
    req = requirement("REQ-1")

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analyzed("REQ-unknown")), (req,), {req.requirement_id: ()})


def test_validate_submission_rejects_duplicate_rule_ids() -> None:
    req = requirement("REQ-1")
    applicable = (rule("acceptance.criteria"),)
    analysis = analyzed(
        req.requirement_id,
        checks=(
            check("acceptance.criteria", CheckStatus.COMPLETE),
            check(
                "acceptance.criteria",
                CheckStatus.MISSING,
                question="请确认 acceptance.criteria 的缺口？",
            ),
        ),
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_validate_submission_rejects_unknown_rule_ids() -> None:
    req = requirement("REQ-1")
    applicable = (rule("acceptance.criteria"),)
    analysis = analyzed(req.requirement_id, checks=(check("unknown.rule", CheckStatus.COMPLETE),))

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_validate_submission_rejects_missing_rule_ids() -> None:
    req = requirement("REQ-1")
    applicable = (
        rule("acceptance.criteria"),
        rule("timeout.retry", scenario_category="recovery"),
    )
    analysis = analyzed(
        req.requirement_id, checks=(check("acceptance.criteria", CheckStatus.COMPLETE),)
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_validate_submission_rejects_unknown_evidence() -> None:
    req = requirement("REQ-1")
    applicable = (rule("acceptance.criteria"),)
    analysis = analyzed(
        req.requirement_id,
        checks=(
            check(
                "acceptance.criteria",
                CheckStatus.COMPLETE,
                evidence=(source(9, "Unknown quote", section="9.9 Missing"),),
                finding_type=FindingType.FACT,
            ),
        ),
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_validate_submission_maps_fact_validator_failure_to_analysis_invalid() -> None:
    req = requirement("REQ-1")
    applicable = (rule("acceptance.criteria"),)
    payload = {
        "schema_version": "1.0",
        "requirements": [
            {
                "requirement_id": req.requirement_id,
                "checks": [
                    {
                        "rule_id": "acceptance.criteria",
                        "status": "complete",
                        "impact": "both",
                        "severity": "important",
                        "finding_type": "fact",
                        "evidence": [],
                        "rationale": "missing evidence",
                        "confidence": 0.9,
                    }
                ],
                "scenarios": [],
            }
        ],
    }

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(payload, (req,), {req.requirement_id: applicable})


def test_validate_submission_rejects_missing_chinese_question() -> None:
    req = requirement("REQ-1")
    applicable = (rule("timeout.retry"),)
    analysis = analyzed(
        req.requirement_id,
        checks=(check("timeout.retry", CheckStatus.MISSING, question="Need retry strategy?"),),
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_validate_submission_rejects_unknown_scenario_category() -> None:
    req = requirement("REQ-1")
    applicable = (rule("timeout.retry", scenario_category="recovery"),)
    analysis = analyzed(
        req.requirement_id,
        checks=(check("timeout.retry", CheckStatus.COMPLETE),),
        scenarios=(scenario("unknown", covered=False),),
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(submission(analysis), (req,), {req.requirement_id: applicable})


def test_scenario_is_covered_only_with_valid_evidence() -> None:
    req = requirement("REQ-1")
    applicable = (rule("timeout.retry", scenario_category="recovery"),)
    payload = {
        "schema_version": "1.0",
        "requirements": [
            {
                "requirement_id": req.requirement_id,
                "checks": [
                    {
                        "rule_id": "timeout.retry",
                        "status": "complete",
                        "impact": "both",
                        "severity": "important",
                        "finding_type": "suggestion",
                        "evidence": [],
                        "rationale": "ok",
                        "confidence": 0.9,
                    }
                ],
                "scenarios": [
                    {
                        "category": "recovery",
                        "description": "Recovery scenario",
                        "covered": True,
                        "evidence": [],
                    }
                ],
            }
        ],
    }

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        validate_submission(payload, (req,), {req.requirement_id: applicable})


def test_validate_submission_accepts_mixed_language_confirmation_question() -> None:
    req = requirement("REQ-1")
    applicable = (rule("timeout.retry", scenario_category="recovery"),)
    valid = analyzed(
        req.requirement_id,
        checks=(
            check(
                "timeout.retry",
                CheckStatus.NEEDS_CONFIRMATION,
                question="请确认 timeout retry policy。",
            ),
        ),
        scenarios=(scenario("recovery", covered=False),),
    )

    analyses = validate_submission(submission(valid), (req,), {req.requirement_id: applicable})

    assert analyses == (valid,)


def test_score_uses_only_applicable_weights() -> None:
    req_id = "REQ-1"
    applicable = rules_by_requirement(
        **{
            req_id: (
                rule("acceptance.criteria", weight=2, scenario_category="baseline"),
                rule("timeout.retry", weight=1, scenario_category="recovery"),
                rule("legacy.manual", weight=9, scenario_category=None),
            )
        }
    )
    analysis = analyzed(
        req_id,
        checks=(
            check("acceptance.criteria", CheckStatus.COMPLETE),
            check(
                "timeout.retry",
                CheckStatus.MISSING,
                question="请确认 timeout retry 策略是什么？",
            ),
            check("legacy.manual", CheckStatus.NOT_APPLICABLE),
        ),
        scenarios=(
            scenario(
                "baseline",
                covered=True,
                evidence=(source(1, "Quote for REQ-1", section="1.0 Overview"),),
            ),
        ),
    )

    score = score_requirements((analysis,), applicable)[0]

    assert score.testability == pytest.approx(66.67)


def test_score_rejects_zero_eligible_weight() -> None:
    req_id = "REQ-1"
    applicable = rules_by_requirement(
        **{req_id: (rule("legacy.manual", weight=2, scenario_category=None),)}
    )
    analysis = analyzed(req_id, checks=(check("legacy.manual", CheckStatus.NOT_APPLICABLE),))

    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        score_requirements((analysis,), applicable)


def test_score_rejects_no_scenario_category() -> None:
    req_id = "REQ-1"
    applicable = rules_by_requirement(
        **{req_id: (rule("acceptance.criteria", weight=1, scenario_category=None),)}
    )
    analysis = analyzed(req_id, checks=(check("acceptance.criteria", CheckStatus.COMPLETE),))

    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        score_requirements((analysis,), applicable)


def test_score_requires_every_applicable_rule_result() -> None:
    req_id = "REQ-1"
    applicable = rules_by_requirement(
        **{
            req_id: (
                rule("acceptance.criteria"),
                rule("timeout.retry", scenario_category="recovery"),
            )
        }
    )
    analysis = analyzed(req_id, checks=(check("acceptance.criteria", CheckStatus.COMPLETE),))

    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        score_requirements((analysis,), applicable)


def test_score_rounds_half_up_to_two_decimals() -> None:
    req_id = "REQ-1"
    applicable = rules_by_requirement(
        **{
            req_id: (
                rule("rule.one", weight=1, scenario_category="baseline"),
                rule("rule.two", weight=1, scenario_category="recovery"),
                rule("rule.three", weight=1, scenario_category="export"),
                rule("rule.four", weight=3, scenario_category="security"),
            )
        }
    )
    analysis = analyzed(
        req_id,
        checks=(
            check("rule.one", CheckStatus.COMPLETE),
            check("rule.two", CheckStatus.COMPLETE),
            check("rule.three", CheckStatus.COMPLETE),
            check("rule.four", CheckStatus.MISSING, question="请确认 security flow。"),
        ),
        scenarios=(
            scenario(
                "baseline",
                covered=True,
                evidence=(source(1, "Quote for REQ-1", section="1.0 Overview"),),
            ),
            scenario("recovery", covered=False),
            scenario("export", covered=False),
            scenario("security", covered=False),
        ),
    )

    score = score_requirements((analysis,), applicable)[0]

    assert score.testability == 50.00


def test_aggregate_scores_uses_equal_arithmetic_mean() -> None:
    req_a = "REQ-a"
    req_b = "REQ-b"
    applicable = rules_by_requirement(
        **{
            req_a: (rule("a.one", weight=2, scenario_category="baseline"),),
            req_b: (
                rule("b.one", weight=1, scenario_category="baseline"),
                rule("b.two", weight=2, scenario_category="recovery"),
            ),
        }
    )
    analyses = (
        analyzed(
            req_a,
            checks=(check("a.one", CheckStatus.COMPLETE),),
            scenarios=(
                scenario(
                    "baseline",
                    covered=True,
                    evidence=(source(1, "Quote for REQ-a", section="1.0 Overview"),),
                ),
            ),
        ),
        analyzed(
            req_b,
            checks=(
                check("b.one", CheckStatus.COMPLETE),
                check("b.two", CheckStatus.MISSING, question="请确认 recovery 条件。"),
            ),
            scenarios=(
                scenario(
                    "baseline",
                    covered=True,
                    evidence=(source(1, "Quote for REQ-b", section="1.0 Overview"),),
                ),
                scenario("recovery", covered=False),
            ),
        ),
    )

    scores = score_requirements(analyses, applicable)
    aggregate = aggregate_scores(scores)

    assert scores[0].testability == 100.00
    assert scores[1].testability == 33.33
    assert aggregate.testability == 66.67
    assert aggregate.scenario_coverage == 75.00


def test_aggregate_scores_rejects_empty_collection() -> None:
    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        aggregate_scores(())


def test_deterministic_scoring_serializes_identically_twice() -> None:
    fixture = json.loads(
        Path("tests/fixtures/provider_submission.json").read_text(encoding="utf-8")
    )
    requirements = (
        requirement(
            "REQ-login-timeout",
            text="Users must log in within 30 seconds and define retry behavior.",
            sources=(
                source(
                    1,
                    "Users must log in within 30 seconds.",
                    section="1.2 Login",
                ),
            ),
        ),
        requirement(
            "REQ-export-format",
            text="The report exports CSV and PDF.",
            sources=(
                source(
                    2,
                    "The report exports CSV and PDF.",
                    section="2.1 Export",
                ),
            ),
        ),
    )
    applicable = rules_by_requirement(
        **{
            "REQ-login-timeout": (
                rule("acceptance.criteria", weight=2, scenario_category="baseline"),
                rule(
                    "timeout.retry",
                    weight=1,
                    scenario_category="recovery",
                    impact=Impact.AUTOMATION,
                ),
                rule(
                    "legacy.manual",
                    weight=5,
                    scenario_category=None,
                    impact=Impact.MANUAL,
                ),
            ),
            "REQ-export-format": (
                rule("export.format", weight=2, scenario_category="export"),
                rule(
                    "export.localization",
                    weight=2,
                    scenario_category="export",
                    impact=Impact.MANUAL,
                ),
            ),
        }
    )

    analyses = validate_submission(fixture, requirements, applicable)
    first = score_requirements(analyses, applicable)
    second = score_requirements(analyses, applicable)

    first_json = json.dumps(
        [item.model_dump() for item in first], ensure_ascii=False, sort_keys=True
    )
    second_json = json.dumps(
        [item.model_dump() for item in second], ensure_ascii=False, sort_keys=True
    )

    assert first_json == second_json
    assert first_json == (
        '[{"requirement_id": "REQ-login-timeout", "scenario_coverage": 50.0, '
        '"testability": 66.67}, {"requirement_id": "REQ-export-format", '
        '"scenario_coverage": 100.0, "testability": 50.0}]'
    )


def test_provider_submission_fixture_is_schema_valid_and_has_no_score() -> None:
    payload: dict[str, Any] = json.loads(
        Path("tests/fixtures/provider_submission.json").read_text(encoding="utf-8")
    )

    AnalysisSubmission.model_validate(payload)
    assert all(
        "score" not in requirement_payload for requirement_payload in payload["requirements"]
    )
