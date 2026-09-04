from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import ANALYSIS_INVALID, ReviewError, ReviewException
from .models import (
    ApplicableRule,
    CheckResult,
    CheckStatus,
    FindingType,
    LogicalRequirement,
    RequirementAnalysis,
    ScenarioResult,
    Severity,
    SourceRef,
)

FAST_SCHEMA_VERSION: Literal["2.0"] = "2.0"
DEFAULT_FAST_BATCH_BYTES = 48_000


class FastRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    category: str | None


class FastRequirementInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    title: str | None
    sources: tuple[str, ...]
    rule_ids: tuple[str, ...]
    hints: tuple[str, ...]
    needs_manual_review: bool


class FastAnalysisBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    batch_id: str
    requirements: tuple[FastRequirementInput, ...]
    rules: dict[str, FastRuleInput]
    instructions: str


class NextFastBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    done: bool
    batch: FastAnalysisBatch | None = None


class CompactVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CheckStatus
    evidence: tuple[int, ...] = ()
    reason: str | None = None
    confidence: float = Field(default=0.7, ge=0, le=1)


class CompactRequirementVerdicts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    verdicts: dict[str, CompactVerdict]


class CompactAnalysisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["2.0"]
    batch_id: str
    items: tuple[CompactRequirementVerdicts, ...]


def _invalid(message: str, **details: object) -> ReviewException:
    return ReviewException(
        ReviewError(code=ANALYSIS_INVALID, message=message, details=details)
    )


def _hints(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    patterns = (
        ("modal", r"\b(?:shall|must|should|required)\b"),
        ("timing", r"\b(?:timeout|deadline|cycle|\d+\s*ms)\b"),
        ("state", r"\b(?:state|mode|transition)\b"),
        ("diagnostic", r"\b(?:dtc|diagnostic|fault|error)\b"),
        ("interface", r"\b(?:interface|signal|can|lin|dbus)\b"),
        ("reset", r"\b(?:reset|wake-?up|power-on|power-off)\b"),
    )
    return tuple(name for name, pattern in patterns if re.search(pattern, folded))


def _requirement_input(
    requirement: LogicalRequirement,
    rules: tuple[ApplicableRule, ...],
) -> FastRequirementInput:
    return FastRequirementInput(
        requirement_id=requirement.requirement_id,
        title=requirement.title,
        sources=tuple(source.quote for source in requirement.sources),
        rule_ids=tuple(rule.rule_id for rule in rules),
        hints=_hints(requirement.text),
        needs_manual_review=requirement.needs_manual_review,
    )


def _build_batch(
    run_id: str,
    requirements: tuple[FastRequirementInput, ...],
    rule_by_id: Mapping[str, ApplicableRule],
) -> FastAnalysisBatch:
    ordered_rule_ids = tuple(
        dict.fromkeys(rule_id for item in requirements for rule_id in item.rule_ids)
    )
    rules = {
        rule_id: FastRuleInput(
            question=rule_by_id[rule_id].question,
            category=rule_by_id[rule_id].scenario_category,
        )
        for rule_id in ordered_rule_ids
    }
    identity = json.dumps(
        {
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "rules": {key: value.model_dump(mode="json") for key, value in rules.items()},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    batch_id = f"fast-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"
    return FastAnalysisBatch(
        run_id=run_id,
        batch_id=batch_id,
        requirements=requirements,
        rules=rules,
        instructions=(
            "逐条返回每个 requirement_id 的全部 rule_id。仅输出 schema_version、"
            "batch_id 和 items；evidence 使用 sources 的零基索引。complete 必须引用证据，"
            "missing 或 needs_confirmation 用中文 reason。"
        ),
    )


def _serialized_size(batch: FastAnalysisBatch) -> int:
    return len(
        json.dumps(
            batch.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def build_fast_batches(
    run_id: str,
    requirements: tuple[LogicalRequirement, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
    *,
    max_batch_bytes: int = DEFAULT_FAST_BATCH_BYTES,
) -> tuple[FastAnalysisBatch, ...]:
    if max_batch_bytes < 1:
        raise ValueError("max_batch_bytes must be positive")
    expected_ids = {item.requirement_id for item in requirements}
    if set(applicable) != expected_ids:
        raise _invalid("逻辑需求与适用规则映射不一致")

    rule_by_id: dict[str, ApplicableRule] = {}
    inputs: list[FastRequirementInput] = []
    for requirement in requirements:
        rules = applicable[requirement.requirement_id]
        for rule in rules:
            rule_by_id.setdefault(rule.rule_id, rule)
        inputs.append(_requirement_input(requirement, rules))

    batches: list[FastAnalysisBatch] = []
    current: list[FastRequirementInput] = []
    for item in inputs:
        candidate = _build_batch(run_id, tuple([*current, item]), rule_by_id)
        if current and _serialized_size(candidate) > max_batch_bytes:
            batches.append(_build_batch(run_id, tuple(current), rule_by_id))
            current = [item]
        else:
            current.append(item)
    if current:
        batches.append(_build_batch(run_id, tuple(current), rule_by_id))
    return tuple(batches)


def _exact_ids(actual: tuple[str, ...], expected: set[str], label: str) -> None:
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise _invalid(
            f"compact {label} 集合不一致",
            missing=sorted(expected - set(actual)),
            unknown=sorted(set(actual) - expected),
        )


def _unique_sources(sources: list[SourceRef]) -> tuple[SourceRef, ...]:
    unique: list[SourceRef] = []
    seen: set[tuple[object, ...]] = set()
    for source in sources:
        key = (source.page, source.section, source.table_index, source.quote)
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return tuple(unique)


def expand_compact_submission(
    batch: FastAnalysisBatch,
    submission: CompactAnalysisSubmission,
    requirements: tuple[LogicalRequirement, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
) -> tuple[RequirementAnalysis, ...]:
    if submission.batch_id != batch.batch_id:
        raise _invalid("compact batch_id 不匹配", expected=batch.batch_id)
    batch_ids = tuple(item.requirement_id for item in batch.requirements)
    _exact_ids(
        tuple(item.requirement_id for item in submission.items),
        set(batch_ids),
        "requirement",
    )
    requirement_by_id = {item.requirement_id: item for item in requirements}
    submitted_by_id = {item.requirement_id: item for item in submission.items}
    analyses: list[RequirementAnalysis] = []

    for requirement_id in batch_ids:
        requirement = requirement_by_id[requirement_id]
        rules = applicable[requirement_id]
        rule_by_id = {rule.rule_id: rule for rule in rules}
        submitted = submitted_by_id[requirement_id]
        _exact_ids(tuple(submitted.verdicts), set(rule_by_id), "rule")
        checks: list[CheckResult] = []
        category_evidence: dict[str, list[SourceRef]] = {}

        for rule in rules:
            verdict = submitted.verdicts[rule.rule_id]
            if any(index < 0 or index >= len(requirement.sources) for index in verdict.evidence):
                raise _invalid(
                    "compact evidence 索引越界",
                    requirement_id=requirement_id,
                    rule_id=rule.rule_id,
                )
            evidence = tuple(requirement.sources[index] for index in verdict.evidence)
            if verdict.status is CheckStatus.COMPLETE and not evidence:
                raise _invalid(
                    "complete verdict 必须提供 evidence",
                    requirement_id=requirement_id,
                    rule_id=rule.rule_id,
                )
            is_gap = verdict.status in {
                CheckStatus.MISSING,
                CheckStatus.NEEDS_CONFIRMATION,
            }
            checks.append(
                CheckResult(
                    rule_id=rule.rule_id,
                    status=verdict.status,
                    impact=rule.impact,
                    severity=(
                        Severity.IMPORTANT
                        if is_gap and rule.weight >= 5
                        else Severity.NORMAL
                    ),
                    finding_type=(
                        FindingType.FACT
                        if verdict.status is CheckStatus.COMPLETE
                        else FindingType.SUGGESTION
                    ),
                    evidence=evidence,
                    rationale=verdict.reason,
                    question=rule.question if is_gap else None,
                    confidence=verdict.confidence,
                )
            )
            if (
                verdict.status is CheckStatus.COMPLETE
                and rule.scenario_category is not None
            ):
                category_evidence.setdefault(rule.scenario_category, []).extend(evidence)

        categories = tuple(
            dict.fromkeys(rule.scenario_category for rule in rules if rule.scenario_category)
        )
        scenarios = tuple(
            ScenarioResult(
                category=category,
                description=(
                    "该类别已有明确需求证据。"
                    if category in category_evidence
                    else "该类别尚缺少可确认的需求证据。"
                ),
                covered=category in category_evidence,
                evidence=_unique_sources(category_evidence.get(category, [])),
            )
            for category in categories
        )
        analyses.append(
            RequirementAnalysis(
                requirement_id=requirement_id,
                checks=tuple(checks),
                scenarios=scenarios,
            )
        )
    return tuple(analyses)


__all__ = [
    "CompactAnalysisSubmission",
    "CompactRequirementVerdicts",
    "CompactVerdict",
    "DEFAULT_FAST_BATCH_BYTES",
    "FAST_SCHEMA_VERSION",
    "FastAnalysisBatch",
    "NextFastBatch",
    "build_fast_batches",
    "expand_compact_submission",
]