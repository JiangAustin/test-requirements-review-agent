from pathlib import Path

import pytest

from requirements_review_agent.errors import RULE_PACK_INVALID, ReviewException
from requirements_review_agent.models import AtomicRequirement, SourceRef


def requirement(text: str) -> AtomicRequirement:
    return AtomicRequirement(
        requirement_id="r1",
        text=text,
        sources=(SourceRef(page=1, quote="q"),),
    )


def test_wifi_requirement_selects_connectivity_and_recovery_rules() -> None:
    from requirements_review_agent.rules_impl import (
        load_rule_pack,
        select_applicable_rules,
    )

    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    rules = select_applicable_rules(requirement("App connects through Wi-Fi"), pack)
    assert {rule.rule_id for rule in rules} >= {
        "connectivity.preconditions",
        "recovery.connection_loss",
        "automation.observability",
    }


def test_invalid_weight_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: '1.0'\nrules:\n- id: bad\n  weight: 0\n", encoding="utf-8")
    from requirements_review_agent.rules_impl import load_rule_pack

    with pytest.raises(ReviewException) as exc:
        load_rule_pack(path)

    assert RULE_PACK_INVALID in str(exc.value)
