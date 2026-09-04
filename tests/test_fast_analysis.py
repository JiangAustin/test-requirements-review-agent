from __future__ import annotations

import pytest

from requirements_review_agent.errors import ANALYSIS_INVALID, ReviewException
from requirements_review_agent.fast_analysis import (
    CompactAnalysisSubmission,
    CompactRequirementVerdicts,
    CompactVerdict,
    build_fast_batches,
    expand_compact_submission,
)
from requirements_review_agent.models import (
    ApplicableRule,
    CheckStatus,
    Impact,
    LogicalRequirement,
    SourceRef,
)


def logical(requirement_id: str, text: str, page: int = 1) -> LogicalRequirement:
    return LogicalRequirement(
        requirement_id=requirement_id,
        title=f"Title {requirement_id}",
        text=text,
        sources=(SourceRef(page=page, quote=text, section=f"{requirement_id} - title"),),
        external_id=requirement_id,
    )


def rule(rule_id: str, category: str, weight: int = 5) -> ApplicableRule:
    return ApplicableRule(
        rule_id=rule_id,
        question=f"请确认 {rule_id} 已明确定义。",
        weight=weight,
        impact=Impact.BOTH,
        scenario_category=category,
        always=True,
        keywords=(),
    )


def test_builds_stable_compact_batches_with_rules_once() -> None:
    requirements = (
        logical("VESW-1", "The ECU shall start."),
        logical("VESW-2", "The ECU shall stop.", page=2),
    )
    applicable = {
        "VESW-1": (rule("behavior.acceptance", "behavior"),),
        "VESW-2": (rule("behavior.acceptance", "behavior"),),
    }

    first = build_fast_batches("run-1", requirements, applicable, max_batch_bytes=50_000)
    second = build_fast_batches("run-1", requirements, applicable, max_batch_bytes=50_000)

    assert first == second
    assert len(first) == 1
    assert tuple(first[0].rules) == ("behavior.acceptance",)
    assert [item.requirement_id for item in first[0].requirements] == [
        "VESW-1",
        "VESW-2",
    ]
    assert first[0].requirements[0].sources == ("The ECU shall start.",)


def test_byte_budget_never_splits_a_requirement() -> None:
    requirements = (
        logical("VESW-1", "A" * 500),
        logical("VESW-2", "B" * 500),
    )
    applicable = {
        item.requirement_id: (rule("behavior.acceptance", "behavior"),)
        for item in requirements
    }

    batches = build_fast_batches("run-1", requirements, applicable, max_batch_bytes=800)

    assert len(batches) == 2
    assert all(len(batch.requirements) == 1 for batch in batches)


def test_expands_compact_verdicts_with_local_rule_and_evidence_data() -> None:
    requirement = logical("VESW-1", "The ECU shall start.")
    applicable = {
        "VESW-1": (
            rule("behavior.acceptance", "behavior"),
            rule("state.mode", "state", weight=4),
        )
    }
    batch = build_fast_batches(
        "run-1", (requirement,), applicable, max_batch_bytes=50_000
    )[0]
    submission = CompactAnalysisSubmission(
        schema_version="2.0",
        batch_id=batch.batch_id,
        items=(
            CompactRequirementVerdicts(
                requirement_id="VESW-1",
                verdicts={
                    "behavior.acceptance": CompactVerdict(
                        status=CheckStatus.COMPLETE,
                        evidence=(0,),
                        confidence=0.9,
                    ),
                    "state.mode": CompactVerdict(
                        status=CheckStatus.MISSING,
                        evidence=(),
                        reason="未说明目标状态。",
                        confidence=0.8,
                    ),
                },
            ),
        ),
    )

    analyses = expand_compact_submission(
        batch, submission, (requirement,), applicable
    )

    assert analyses[0].checks[0].evidence == requirement.sources
    assert analyses[0].checks[0].impact is Impact.BOTH
    assert analyses[0].checks[1].question == "请确认 state.mode 已明确定义。"
    assert {(item.category, item.covered) for item in analyses[0].scenarios} == {
        ("behavior", True),
        ("state", False),
    }


@pytest.mark.parametrize("failure", ["missing_requirement", "unknown_rule", "bad_evidence"])
def test_rejects_incomplete_or_invalid_compact_verdicts(failure: str) -> None:
    requirement = logical("VESW-1", "The ECU shall start.")
    applicable = {
        "VESW-1": (rule("behavior.acceptance", "behavior"),),
    }
    batch = build_fast_batches(
        "run-1", (requirement,), applicable, max_batch_bytes=50_000
    )[0]
    items: tuple[CompactRequirementVerdicts, ...] = ()
    if failure != "missing_requirement":
        rule_id = "unknown.rule" if failure == "unknown_rule" else "behavior.acceptance"
        evidence = (4,) if failure == "bad_evidence" else (0,)
        items = (
            CompactRequirementVerdicts(
                requirement_id="VESW-1",
                verdicts={
                    rule_id: CompactVerdict(
                        status=CheckStatus.COMPLETE,
                        evidence=evidence,
                        confidence=0.9,
                    )
                },
            ),
        )
    submission = CompactAnalysisSubmission(
        schema_version="2.0", batch_id=batch.batch_id, items=items
    )

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        expand_compact_submission(batch, submission, (requirement,), applicable)