from .rules_impl import load_rule_pack, select_applicable_rules

__all__ = ["load_rule_pack", "select_applicable_rules"]
from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict

from .errors import RULE_PACK_INVALID, ReviewError, ReviewException
from .models import ApplicableRule, AtomicRequirement, RuleCheck


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    rules: tuple[RuleCheck, ...]

    @classmethod
    def from_dict(cls, data: dict[str, object], path: Path | None = None) -> "RulePack":
        try:
            version = data.get("version")
            if not isinstance(version, str):
                details_raw = {
                    "path": str(path) if path is not None else None,
                    "error": "invalid version",
                }
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid version", details=details))

            raw_rules_obj = data.get("rules")
            if raw_rules_obj is None:
                raw_rules = []
            elif not isinstance(raw_rules_obj, list):
                details_raw = {
                    "path": str(path) if path is not None else None,
                    "error": "rules must be a list",
                }
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid rules", details=details))
            else:
                raw_rules = raw_rules_obj

            checks: list[RuleCheck] = []
            for r in raw_rules:
                r = dict(r)
                if "id" in r:
                    r["rule_id"] = r.pop("id")
                if "keywords" in r and r["keywords"] is not None:
                    r["keywords"] = tuple(r["keywords"])
                else:
                    r["keywords"] = tuple()
                checks.append(RuleCheck.model_validate(r))

            pack = cls(version=version, rules=tuple(checks))
        except ReviewException:
            raise
        except Exception as exc:
            details_raw = {"path": str(path) if path is not None else None, "error": str(exc)}
            details = cast(dict[str, object], details_raw)
            err = ReviewError(code=RULE_PACK_INVALID, message="Invalid rule pack", details=details)
            raise ReviewException(err) from exc

        ids = [r.rule_id for r in pack.rules]
        if len(ids) != len(set(ids)):
            details_raw = {"path": str(path) if path is not None else None, "error": "duplicate rule ids"}
            details = cast(dict[str, object], details_raw)
            raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Duplicate rule ids", details=details))

        if not any(r.always for r in pack.rules):
            details_raw = {"path": str(path) if path is not None else None, "error": "no always rule"}
            details = cast(dict[str, object], details_raw)
            raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="No always rule", details=details))

        for r in pack.rules:
            if not r.question or not r.question.strip():
                details_raw = {"path": str(path) if path is not None else None, "error": f"empty question for {r.rule_id}"}
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Empty question", details=details))

        return pack


def load_rule_pack(path: Path) -> RulePack:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("rule pack must be a mapping")
        return RulePack.from_dict(data, path=path)
    except ReviewException:
        raise
    except Exception as exc:
        details_raw = {"path": str(path), "error": str(exc)}
        details = cast(dict[str, object], details_raw)
        raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Failed to load rule pack", details=details)) from exc


def select_applicable_rules(
    requirement: AtomicRequirement,
    pack: RulePack,
) -> tuple[ApplicableRule, ...]:
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())

    text = normalize(requirement.text)
    selected: list[ApplicableRule] = []
    for rule in pack.rules:
        if rule.always:
            selected.append(ApplicableRule.model_validate(rule.model_dump()))
            continue
        if any(normalize(kw) in text for kw in rule.keywords):
            selected.append(ApplicableRule.model_validate(rule.model_dump()))

    out: dict[str, ApplicableRule] = {}
    for r in selected:
        if r.rule_id not in out:
            out[r.rule_id] = r
    return tuple(out.values())
from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict

from .errors import RULE_PACK_INVALID, ReviewError, ReviewException
from .models import ApplicableRule, AtomicRequirement, RuleCheck


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    rules: tuple[RuleCheck, ...]

    @classmethod
    def from_dict(cls, data: dict[str, object], path: Path | None = None) -> "RulePack":
        try:
            version = data.get("version")
            if not isinstance(version, str):
                details_raw = {
                    "path": str(path) if path is not None else None,
                    "error": "invalid version",
                }
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid version", details=details))

            raw_rules_obj = data.get("rules")
            if raw_rules_obj is None:
                raw_rules = []
            elif not isinstance(raw_rules_obj, list):
                details_raw = {
                    "path": str(path) if path is not None else None,
                    "error": "rules must be a list",
                }
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid rules", details=details))
            else:
                raw_rules = raw_rules_obj

            checks: list[RuleCheck] = []
            for r in raw_rules:
                # expect mapping-like items
                r = dict(r)
                if "id" in r:
                    r["rule_id"] = r.pop("id")
                if "keywords" in r and r["keywords"] is not None:
                    r["keywords"] = tuple(r["keywords"])
                else:
                    r["keywords"] = tuple()
                checks.append(RuleCheck.model_validate(r))

            pack = cls(version=version, rules=tuple(checks))
        except ReviewException:
            raise
        except Exception as exc:
            details_raw = {"path": str(path) if path is not None else None, "error": str(exc)}
            details = cast(dict[str, object], details_raw)
            err = ReviewError(code=RULE_PACK_INVALID, message="Invalid rule pack", details=details)
            raise ReviewException(err) from exc

        ids = [r.rule_id for r in pack.rules]
        if len(ids) != len(set(ids)):
            details_raw = {"path": str(path) if path is not None else None, "error": "duplicate rule ids"}
            details = cast(dict[str, object], details_raw)
            raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Duplicate rule ids", details=details))

        if not any(r.always for r in pack.rules):
            details_raw = {"path": str(path) if path is not None else None, "error": "no always rule"}
            details = cast(dict[str, object], details_raw)
            raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="No always rule", details=details))

        for r in pack.rules:
            if not r.question or not r.question.strip():
                details_raw = {"path": str(path) if path is not None else None, "error": f"empty question for {r.rule_id}"}
                details = cast(dict[str, object], details_raw)
                raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Empty question", details=details))

        return pack


def load_rule_pack(path: Path) -> RulePack:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("rule pack must be a mapping")
        return RulePack.from_dict(data, path=path)
    except ReviewException:
        raise
    except Exception as exc:
        details_raw = {"path": str(path), "error": str(exc)}
        details = cast(dict[str, object], details_raw)
        raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Failed to load rule pack", details=details)) from exc


def select_applicable_rules(
    requirement: AtomicRequirement,
    pack: RulePack,
) -> tuple[ApplicableRule, ...]:
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())

    text = normalize(requirement.text)
    selected: list[ApplicableRule] = []
    for rule in pack.rules:
        if rule.always:
            selected.append(ApplicableRule.model_validate(rule.model_dump()))
            continue
        if any(normalize(kw) in text for kw in rule.keywords):
            selected.append(ApplicableRule.model_validate(rule.model_dump()))

    out: dict[str, ApplicableRule] = {}
    for r in selected:
        if r.rule_id not in out:
            out[r.rule_id] = r
    return tuple(out.values())
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .errors import RULE_PACK_INVALID, ReviewError, ReviewException
from .models import ApplicableRule, AtomicRequirement, RuleCheck


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    rules: tuple[RuleCheck, ...]

    @classmethod
    def from_dict(cls, data: dict[str, object], path: Path | None = None) -> RulePack:
        try:
            version = data.get("version")
            if not isinstance(version, str):
                details = {
                    "path": str(path) if path is not None else None,
                    "error": "invalid version",
                }
                    if not isinstance(version, str):
                        details_raw = {
                            "path": str(path) if path is not None else None,
                            "error": "invalid version",
                        }
                        details = cast(dict[str, object], details_raw)
                        raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid version", details=details))
                details = {
                    "path": str(path) if path is not None else None,
                    "error": "rules must be a list",
                }
                        details_raw = {
                            "path": str(path) if path is not None else None,
                            "error": "rules must be a list",
                        }
                        details = cast(dict[str, object], details_raw)
                        raise ReviewException(ReviewError(code=RULE_PACK_INVALID, message="Invalid rules", details=details))
                # map alias id -> rule_id expected by RuleCheck
                r = dict(r)
                if "id" in r:
                    r["rule_id"] = r.pop("id")
                # ensure keywords is tuple
                if "keywords" in r and r["keywords"] is not None:
                    r["keywords"] = tuple(r["keywords"])
                else:
                    r["keywords"] = tuple()
                checks.append(RuleCheck.model_validate(r))

            pack = cls(version=version, rules=tuple(checks))
        except Exception as exc:  # include pydantic/yaml errors
            details: dict[str, object] = {
                "path": str(path) if path is not None else None,
                "error": str(exc),
            }
                    details_raw = {
                        "path": str(path) if path is not None else None,
                        "error": str(exc),
                    }
                    details = cast(dict[str, object], details_raw)
                    err = ReviewError(code=RULE_PACK_INVALID, message="Invalid rule pack", details=details)
                    raise ReviewException(err) from exc
            details = {
                "path": str(path) if path is not None else None,
                "error": "duplicate rule ids",
            }
                    details_raw = {
                        "path": str(path) if path is not None else None,
                        "error": "duplicate rule ids",
                    }
                    details = cast(dict[str, object], details_raw)
                    raise ReviewException(
                        ReviewError(code=RULE_PACK_INVALID, message="Duplicate rule ids", details=details)
                    )
                "error": "no always rule",
            }
                    details_raw = {
                        "path": str(path) if path is not None else None,
                        "error": "no always rule",
                    }
                    details = cast(dict[str, object], details_raw)
                    raise ReviewException(
                        ReviewError(code=RULE_PACK_INVALID, message="No always rule", details=details)
                    )
                    "path": str(path) if path is not None else None,
                    "error": f"empty question for {r.rule_id}",
                }
                        details_raw = {
                            "path": str(path) if path is not None else None,
                            "error": f"empty question for {r.rule_id}",
                        }
                        details = cast(dict[str, object], details_raw)
                        raise ReviewException(
                            ReviewError(code=RULE_PACK_INVALID, message="Empty question", details=details)
                        )
def load_rule_pack(path: Path) -> RulePack:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("rule pack must be a mapping")
        return RulePack.from_dict(data, path=path)
    except ReviewException:
        raise
    except Exception as exc:
        details: dict[str, object] = {"path": str(path), "error": str(exc)}
        raise ReviewException(
            ReviewError(code=RULE_PACK_INVALID, message="Failed to load rule pack", details=details)
        ) from exc
                details_raw = {"path": str(path), "error": str(exc)}
                details = cast(dict[str, object], details_raw)
                raise ReviewException(
                    ReviewError(code=RULE_PACK_INVALID, message="Failed to load rule pack", details=details)
                ) from exc
    pack: RulePack,
) -> tuple[ApplicableRule, ...]:
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())

    text = normalize(requirement.text)
    selected: list[ApplicableRule] = []
    for rule in pack.rules:
        if rule.always:
            selected.append(ApplicableRule.model_validate(rule.model_dump()))
            continue
        if any(normalize(kw) in text for kw in rule.keywords):
            selected.append(ApplicableRule.model_validate(rule.model_dump()))

    # dedupe preserving order: keep first occurrence
    out: dict[str, ApplicableRule] = {}
    for r in selected:
        if r.rule_id not in out:
            out[r.rule_id] = r
    return tuple(out.values())
