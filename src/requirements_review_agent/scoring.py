from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from .errors import RULE_PACK_INVALID, ReviewError, ReviewException
from .models import (
    AggregateScore,
    ApplicableRule,
    CheckStatus,
    RequirementAnalysis,
    RequirementScore,
)

TWOPLACES = Decimal("0.01")
HUNDRED = Decimal("100")


def _rule_pack_invalid(message: str, **details: object) -> ReviewException:
    return ReviewException(ReviewError(code=RULE_PACK_INVALID, message=message, details=details))


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise _rule_pack_invalid("分母必须大于 0", denominator=denominator)
    percent = (Decimal(numerator) * HUNDRED / Decimal(denominator)).quantize(
        TWOPLACES,
        rounding=ROUND_HALF_UP,
    )
    return float(percent)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        raise _rule_pack_invalid("空分数集合无法聚合")
    total = sum((Decimal(str(value)) for value in values), start=Decimal("0"))
    return float((total / Decimal(len(values))).quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def score_requirements(
    analyses: tuple[RequirementAnalysis, ...],
    applicable: Mapping[str, tuple[ApplicableRule, ...]],
) -> tuple[RequirementScore, ...]:
    scores: list[RequirementScore] = []
    applicable_ids = set(applicable)
    analysis_ids = {analysis.requirement_id for analysis in analyses}
    if analysis_ids != applicable_ids:
        raise _rule_pack_invalid(
            "分析结果与适用规则映射不一致",
            missing=sorted(applicable_ids - analysis_ids),
            extra=sorted(analysis_ids - applicable_ids),
        )

    for analysis in analyses:
        rules = tuple(applicable.get(analysis.requirement_id, ()))
        if not rules:
            raise _rule_pack_invalid(
                "需求缺少适用规则",
                requirement_id=analysis.requirement_id,
            )

        result_by_rule = {item.rule_id: item for item in analysis.checks}
        expected_rule_ids = {rule.rule_id for rule in rules}
        if set(result_by_rule) != expected_rule_ids:
            raise _rule_pack_invalid(
                "需求未覆盖全部适用规则",
                requirement_id=analysis.requirement_id,
                missing=sorted(expected_rule_ids - set(result_by_rule)),
                extra=sorted(set(result_by_rule) - expected_rule_ids),
            )

        eligible_rules = []
        for rule in rules:
            if result_by_rule[rule.rule_id].status is CheckStatus.NOT_APPLICABLE:
                continue
            eligible_rules.append(rule)
        eligible_weight = sum(rule.weight for rule in eligible_rules)
        if eligible_weight <= 0:
            raise _rule_pack_invalid(
                "需求没有可计分的适用规则",
                requirement_id=analysis.requirement_id,
            )

        completed_weight = sum(
            rule.weight
            for rule in eligible_rules
            if result_by_rule[rule.rule_id].status is CheckStatus.COMPLETE
        )
        testability = _percent(completed_weight, eligible_weight)

        categories = {rule.scenario_category for rule in rules if rule.scenario_category}
        if not categories:
            raise _rule_pack_invalid(
                "需求没有适用的场景类别",
                requirement_id=analysis.requirement_id,
            )

        covered_categories = {
            item.category for item in analysis.scenarios if item.covered and item.evidence
        }
        scenario_coverage = _percent(len(covered_categories & categories), len(categories))

        scores.append(
            RequirementScore(
                requirement_id=analysis.requirement_id,
                testability=testability,
                scenario_coverage=scenario_coverage,
            )
        )

    return tuple(scores)


def aggregate_scores(scores: tuple[RequirementScore, ...]) -> AggregateScore:
    if not scores:
        raise _rule_pack_invalid("空分数集合无法聚合")

    return AggregateScore(
        testability=_mean(tuple(score.testability for score in scores)),
        scenario_coverage=_mean(tuple(score.scenario_coverage for score in scores)),
    )


__all__ = ["aggregate_scores", "score_requirements"]
