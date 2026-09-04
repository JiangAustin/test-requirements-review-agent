from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from .errors import RULE_PACK_INVALID, ReviewError, ReviewException
from .models import ApplicableRule, AtomicRequirement, RuleCheck

QUESTION_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(..., min_length=1)
    rules: tuple[RuleCheck, ...]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version must not be blank")
        return value


class RawRuleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    weight: int
    impact: str
    scenario_category: str | None
    always: bool
    keywords: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        if QUESTION_CJK_RE.search(value) is None:
            raise ValueError("must contain at least one Chinese character")
        return value

    @field_validator("scenario_category")
    @classmethod
    def validate_optional_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class RawRulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(..., min_length=1)
    rules: tuple[RawRuleEntry, ...]

    @field_validator("version")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version must not be blank")
        return value


def _short_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first_error = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first_error.get("loc", ()))
        message = str(first_error.get("msg", exc.__class__.__name__))
        return f"{location}: {message}" if location else message

    message = str(exc).strip().splitlines()[0]
    return message or exc.__class__.__name__


def _invalid_rule_pack(path: Path, exc: Exception) -> ReviewException:
    details: dict[str, object] = {
        "path": str(path),
        "exception_type": exc.__class__.__name__,
        "message": _short_message(exc)[:200],
    }
    return ReviewException(
        ReviewError(code=RULE_PACK_INVALID, message="Invalid rule pack", details=details)
    )


def _build_rule_check(raw_rule: RawRuleEntry) -> RuleCheck:
    return RuleCheck.model_validate(
        {
            "rule_id": raw_rule.id,
            "question": raw_rule.question,
            "weight": raw_rule.weight,
            "impact": raw_rule.impact,
            "scenario_category": raw_rule.scenario_category,
            "always": raw_rule.always,
            "keywords": raw_rule.keywords,
        }
    )


def _validate_rule_pack(path: Path, pack: RulePack) -> None:
    seen_rule_ids: set[str] = set()
    has_always_rule = False

    for rule in pack.rules:
        if rule.rule_id in seen_rule_ids:
            raise _invalid_rule_pack(path, ValueError(f"duplicate rule id: {rule.rule_id}"))
        seen_rule_ids.add(rule.rule_id)

        if not rule.question.strip():
            raise _invalid_rule_pack(path, ValueError(f"blank question: {rule.rule_id}"))

        if rule.always:
            has_always_rule = True
            if rule.scenario_category is None:
                raise _invalid_rule_pack(
                    path,
                    ValueError(f"always rule requires scenario_category: {rule.rule_id}"),
                )

    if not has_always_rule:
        raise _invalid_rule_pack(path, ValueError("at least one always rule is required"))


def load_rule_pack(path: Path) -> RulePack:
    try:
        loaded: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("rule pack root must be a mapping")

        raw_pack = RawRulePack.model_validate(loaded)
        pack = RulePack(
            version=raw_pack.version,
            rules=tuple(_build_rule_check(raw_rule) for raw_rule in raw_pack.rules),
        )
        _validate_rule_pack(path, pack)
        return pack
    except ReviewException:
        raise
    except (OSError, yaml.YAMLError, ValidationError, TypeError, ValueError) as exc:
        raise _invalid_rule_pack(path, exc) from exc


def load_bundled_rule_pack(name: str) -> RulePack:
    filename = f"{name}.yaml"
    resource = files("requirements_review_agent").joinpath("resources", "rules", filename)
    try:
        loaded: Any = yaml.safe_load(resource.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("rule pack root must be a mapping")
        raw_pack = RawRulePack.model_validate(loaded)
        pack = RulePack(
            version=raw_pack.version,
            rules=tuple(_build_rule_check(raw_rule) for raw_rule in raw_pack.rules),
        )
        _validate_rule_pack(Path(f"built-in:{filename}"), pack)
        return pack
    except ReviewException:
        raise
    except (
        FileNotFoundError,
        OSError,
        yaml.YAMLError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise _invalid_rule_pack(Path(f"built-in:{filename}"), exc) from exc


def _keyword_matches(text: str, keyword: str) -> bool:
    pattern = rf"(?<!\w){re.escape(keyword.casefold())}(?!\w)"
    return re.search(pattern, text) is not None


def select_applicable_rules(
    requirement: AtomicRequirement,
    pack: RulePack,
) -> tuple[ApplicableRule, ...]:
    text = requirement.text.casefold()
    applicable_rules: list[ApplicableRule] = []
    seen_rule_ids: set[str] = set()

    for rule in pack.rules:
        keyword_matches = any(_keyword_matches(text, keyword) for keyword in rule.keywords)
        if not rule.always and not keyword_matches:
            continue

        if rule.rule_id in seen_rule_ids:
            continue

        applicable_rules.append(ApplicableRule.model_validate(rule.model_dump()))
        seen_rule_ids.add(rule.rule_id)

    return tuple(applicable_rules)


__all__ = ["RulePack", "load_rule_pack", "select_applicable_rules"]
