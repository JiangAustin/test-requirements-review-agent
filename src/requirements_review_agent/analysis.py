from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ANALYSIS_INVALID, RULE_PACK_INVALID, ReviewError, ReviewException
from .models import (
    AnalysisSubmission,
    ApplicableRule,
    AtomicRequirement,
    CheckStatus,
    RequirementAnalysis,
    SourceRef,
)

QUESTION_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ANALYSIS_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class AnalysisBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(..., min_length=1)
    batch_index: int = Field(..., ge=0)
    requirements: tuple[AtomicRequirement, ...]
    applicable: dict[str, tuple[ApplicableRule, ...]]
    instructions: str = Field(..., min_length=1)
    analysis_submission_schema: dict[str, Any]


def _error(code: str, message: str, **details: object) -> ReviewException:
    compact_details = {key: value for key, value in details.items() if value is not None}
    return ReviewException(ReviewError(code=code, message=message, details=compact_details))


def _analysis_invalid(message: str, **details: object) -> ReviewException:
    return _error(ANALYSIS_INVALID, message, **details)


def _rule_pack_invalid(message: str, **details: object) -> ReviewException:
    return _error(RULE_PACK_INVALID, message, **details)


def _schema_snapshot() -> dict[str, Any]:
    schema_json = json.dumps(
        AnalysisSubmission.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return cast(dict[str, Any], json.loads(schema_json))


def _source_ref_identity_key(source: SourceRef) -> tuple[object, object, object, object]:
    return (
        source.page,
        source.section,
        source.table_index,
        source.quote,
    )


def _ordered_applicable(
    requirements: tuple[AtomicRequirement, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
) -> dict[str, tuple[ApplicableRule, ...]]:
    ordered: dict[str, tuple[ApplicableRule, ...]] = {}
    requirement_ids = [requirement.requirement_id for requirement in requirements]
    if len(set(requirement_ids)) != len(requirement_ids):
        raise _rule_pack_invalid("批次需求存在重复 requirement_id")

    applicable_ids = set(applicable)
    expected_ids = set(requirement_ids)
    if applicable_ids != expected_ids:
        missing_ids = sorted(expected_ids - applicable_ids)
        extra_ids = sorted(applicable_ids - expected_ids)
        raise _rule_pack_invalid(
            "需求与适用规则映射不一致",
            missing_ids=missing_ids,
            extra_ids=extra_ids,
        )

    for requirement in requirements:
        rules = tuple(applicable[requirement.requirement_id])
        rule_ids = [rule.rule_id for rule in rules]
        if len(set(rule_ids)) != len(rule_ids):
            duplicates = sorted({rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1})
            raise _rule_pack_invalid(
                "需求的适用规则存在重复 rule_id",
                requirement_id=requirement.requirement_id,
                duplicates=duplicates,
            )
        ordered[requirement.requirement_id] = rules
    return ordered


def build_analysis_batch(
    run_id: str,
    batch_index: int,
    requirements: tuple[AtomicRequirement, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
) -> AnalysisBatch:
    ordered_requirements = tuple(requirements)
    ordered_applicable = _ordered_applicable(ordered_requirements, applicable)
    instructions = (
        "请逐条分析每个 AtomicRequirement，并且仅基于提供的原文证据输出 AnalysisSubmission JSON。"
        "requirements 必须且只能包含本批次全部 requirement_id；每条 checks 必须且只能包含"
        "该 requirement 的全部 applicable rule_id。"
        "所有缺失或待确认项必须给出中文问题；不要添加 score 字段；"
        "rule_id、scenario category 与 evidence 必须严格引用批次中提供的数据。"
    )
    return AnalysisBatch(
        run_id=run_id,
        batch_index=batch_index,
        requirements=ordered_requirements,
        applicable=ordered_applicable,
        instructions=instructions,
        analysis_submission_schema=_schema_snapshot(),
    )


def _coerce_submission(submission: AnalysisSubmission | Mapping[str, object]) -> AnalysisSubmission:
    try:
        return AnalysisSubmission.model_validate(submission)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first_error.get("loc", ()))
        raise _analysis_invalid(
            "分析结果结构无效",
            location=location,
            error=str(first_error.get("msg", "validation error"))[:200],
        ) from exc


def _check_exact_ids(items: tuple[str, ...], expected: set[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        seen.add(item)

    unknown = sorted(seen - expected)
    missing = sorted(expected - seen)
    if duplicates or unknown or missing:
        raise _analysis_invalid(
            f"{label} 集合不一致",
            duplicates=sorted(set(duplicates)),
            unknown=unknown,
            missing=missing,
        )


def _require_chinese_question(analysis: RequirementAnalysis) -> None:
    for item in analysis.checks:
        if item.status not in {CheckStatus.MISSING, CheckStatus.NEEDS_CONFIRMATION}:
            continue
        question = (item.question or "").strip()
        if not question or QUESTION_CJK_RE.search(question) is None:
            raise _analysis_invalid(
                "缺失或待确认项必须提供中文问题",
                requirement_id=analysis.requirement_id,
                rule_id=item.rule_id,
            )


def _validate_evidence_membership(
    analysis: RequirementAnalysis,
    requirement: AtomicRequirement,
) -> None:
    valid_source_keys = {_source_ref_identity_key(source) for source in requirement.sources}
    for item in analysis.checks:
        for evidence in item.evidence:
            if _source_ref_identity_key(evidence) not in valid_source_keys:
                raise _analysis_invalid(
                    "结论引用了未知原文证据",
                    requirement_id=analysis.requirement_id,
                    rule_id=item.rule_id,
                )
    for scenario in analysis.scenarios:
        for evidence in scenario.evidence:
            if _source_ref_identity_key(evidence) not in valid_source_keys:
                raise _analysis_invalid(
                    "场景引用了未知原文证据",
                    requirement_id=analysis.requirement_id,
                    category=scenario.category,
                )


def validate_submission(
    submission: AnalysisSubmission | Mapping[str, object],
    requirements: tuple[AtomicRequirement, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
) -> tuple[RequirementAnalysis, ...]:
    parsed = _coerce_submission(submission)
    if parsed.schema_version != ANALYSIS_SCHEMA_VERSION:
        raise _analysis_invalid(
            "分析结果 schema_version 不匹配",
            expected=ANALYSIS_SCHEMA_VERSION,
        )
    ordered_applicable = _ordered_applicable(requirements, applicable)
    requirement_by_id = {requirement.requirement_id: requirement for requirement in requirements}
    expected_ids = set(requirement_by_id)

    analyses = tuple(parsed.requirements)
    _check_exact_ids(tuple(item.requirement_id for item in analyses), expected_ids, "requirement")

    for analysis in analyses:
        requirement = requirement_by_id[analysis.requirement_id]
        applicable_rules = ordered_applicable[analysis.requirement_id]
        expected_rule_ids = {rule.rule_id for rule in applicable_rules}
        _check_exact_ids(tuple(item.rule_id for item in analysis.checks), expected_rule_ids, "rule")
        _require_chinese_question(analysis)
        _validate_evidence_membership(analysis, requirement)

        valid_categories = {
            rule.scenario_category for rule in applicable_rules if rule.scenario_category
        }
        for scenario in analysis.scenarios:
            if scenario.category not in valid_categories:
                raise _analysis_invalid(
                    "场景类别不在适用规则集合中",
                    requirement_id=analysis.requirement_id,
                    category=scenario.category,
                )

    return analyses


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisBatch",
    "build_analysis_batch",
    "validate_submission",
]
