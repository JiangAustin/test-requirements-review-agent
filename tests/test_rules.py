from pathlib import Path

import pytest

from requirements_review_agent.errors import RULE_PACK_INVALID, ReviewException
from requirements_review_agent.models import AtomicRequirement, SourceRef
from requirements_review_agent.rules import load_rule_pack, select_applicable_rules


def requirement(text: str) -> AtomicRequirement:
    return AtomicRequirement(
        requirement_id="r1",
        text=text,
        sources=(SourceRef(page=1, quote="q"),),
    )


def write_pack(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def assert_invalid_rule_pack(path: Path) -> None:
    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        load_rule_pack(path)


def test_wifi_requirement_selects_required_rules() -> None:
    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    rules = select_applicable_rules(requirement("App connects through Wi-Fi"), pack)
    assert {rule.rule_id for rule in rules} >= {
        "connectivity.preconditions",
        "recovery.connection_loss",
        "automation.observability",
    }


def test_bundled_rule_pack_matches_repository_rule_pack() -> None:
    bundled = Path(
        "src/requirements_review_agent/resources/rules/home-iot-v1.yaml"
    ).read_text(encoding="utf-8")
    repository = Path("rules/home-iot-v1.yaml").read_text(encoding="utf-8")

    assert bundled == repository


def test_case_insensitive_keyword_match() -> None:
    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    rules = select_applicable_rules(requirement("APP SUPPORTS WIFI RECOVERY"), pack)
    rule_ids = {rule.rule_id for rule in rules}
    assert "recovery.connection_loss" in rule_ids


def test_unmatched_excludes_non_always_rules() -> None:
    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    rules = select_applicable_rules(requirement("Cabinet color is matte black."), pack)
    assert all(rule.always for rule in rules)


def test_invalid_weight_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "invalid-weight.yaml",
        """
version: "1.0"
rules:
  - id: baseline.always
    question: "请确认 baseline automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: bad.weight
    question: "请确认 invalid weight 已处理。"
    weight: 0
    impact: manual
    scenario_category: validation
    always: false
    keywords: ["wifi"]
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "duplicate-id.yaml",
        """
version: "1.0"
rules:
  - id: duplicate.rule
    question: "请确认 duplicate automation observability 之一。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: duplicate.rule
    question: "请确认 duplicate automation observability 之二。"
    weight: 2
    impact: both
    scenario_category: recovery
    always: false
    keywords: ["wifi"]
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_empty_question_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "empty-question.yaml",
        """
version: "1.0"
rules:
  - id: baseline.always
    question: "请确认 baseline automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: empty.question
    question: ""
    weight: 2
    impact: manual
    scenario_category: validation
    always: false
    keywords: ["wifi"]
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_pure_english_question_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "english-question.yaml",
        """
version: "1.0"
rules:
  - id: baseline.always
    question: "请确认 baseline automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: english.question
    question: "Is the timeout defined?"
    weight: 2
    impact: manual
    scenario_category: validation
    always: false
    keywords: ["wifi"]
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_no_always_rule_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "no-always.yaml",
        """
version: "1.0"
rules:
  - id: no.always
    question: "请确认 recovery strategy 已定义。"
    weight: 1
    impact: both
    scenario_category: recovery
    always: false
    keywords: ["wifi"]
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_no_always_scenario_category_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "no-always-scenario-category.yaml",
        """
version: "1.0"
rules:
  - id: bad.always
    question: "请确认 automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category:
    always: true
    keywords: []
""".strip(),
    )

    assert_invalid_rule_pack(path)


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "malformed.yaml",
        "version: '1.0'\nrules:\n  - id: broken\n    question: [unterminated\n",
    )

    assert_invalid_rule_pack(path)


def test_preserves_yaml_order_for_selected_rules(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "ordered.yaml",
        """
version: "1.0"
rules:
  - id: baseline.always
    question: "请确认 baseline automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: second.keyword
    question: "请确认 Wi-Fi dependency 已声明。"
    weight: 2
    impact: both
    scenario_category: dependency
    always: false
    keywords: ["Wi-Fi"]
  - id: third.keyword
    question: "请确认 app flow 已声明。"
    weight: 3
    impact: manual
    scenario_category: flow
    always: false
    keywords: ["app"]
""".strip(),
    )

    pack = load_rule_pack(path)
    rules = select_applicable_rules(requirement("App requires Wi-Fi pairing."), pack)
    assert tuple(rule.rule_id for rule in rules) == (
        "baseline.always",
        "second.keyword",
        "third.keyword",
    )


def test_default_pack_invariants_and_dimension_coverage() -> None:
    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    always_rules = tuple(rule for rule in pack.rules if rule.always)

    assert pack.version
    assert len(pack.rules) >= 10
    assert always_rules
    assert all(rule.scenario_category for rule in always_rules)
    assert {
        "behavior.acceptance",
        "preconditions.state",
        "boundaries.invalid_input",
        "timeout.retry.recovery",
        "dependency.device_app_cloud",
        "dependency.ble_wifi",
        "permissions.security.privacy",
        "performance.compatibility",
        "automation.interface_data",
        "automation.observability.reset",
    } <= {rule.rule_id for rule in pack.rules}


def test_wifi_exact_punctuation_match_does_not_fold_characters(tmp_path: Path) -> None:
    path = write_pack(
        tmp_path / "wifi-punctuation.yaml",
        """
version: "1.0"
rules:
  - id: baseline.always
    question: "请确认 baseline automation observability 已定义。"
    weight: 1
    impact: automation
    scenario_category: baseline
    always: true
    keywords: []
  - id: wifi.hyphenated
    question: "请确认 Wi-Fi dependency 已声明。"
    weight: 2
    impact: both
    scenario_category: dependency
    always: false
    keywords: ["Wi-Fi"]
""".strip(),
    )

    pack = load_rule_pack(path)
    rules = select_applicable_rules(requirement("The app supports wifi onboarding."), pack)
    assert {rule.rule_id for rule in rules} == {"baseline.always"}
