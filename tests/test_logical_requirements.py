from requirements_review_agent.logical_requirements import group_logical_requirements
from requirements_review_agent.models import AtomicRequirement, SourceRef


def atomic(
    requirement_id: str,
    text: str,
    *,
    page: int,
    section: str | None = None,
    manual: bool = False,
) -> AtomicRequirement:
    return AtomicRequirement(
        requirement_id=requirement_id,
        text=text,
        sources=(SourceRef(page=page, quote=text, section=section),),
        needs_manual_review=manual,
    )


def test_groups_work_item_fragments_and_keeps_standalone_normative_text() -> None:
    heading = 'VESW-3217 - ECU state "initial"'
    requirements = (
        atomic("REQ-1", "The ECU shall enter initial state.", page=10, section=heading),
        atomic(
            "REQ-2",
            "The state is entered after reset.",
            page=11,
            section=heading,
            manual=True,
        ),
        atomic("REQ-3", "Architecture overview.", page=12),
        atomic("REQ-4", "The watchdog must detect a stall.", page=13),
    )

    grouped = group_logical_requirements(requirements)

    assert [item.requirement_id for item in grouped] == ["VESW-3217", "REQ-4"]
    assert grouped[0].external_id == "VESW-3217"
    assert grouped[0].title == 'ECU state "initial"'
    assert grouped[0].text == (
        "The ECU shall enter initial state.\nThe state is entered after reset."
    )
    assert [source.page for source in grouped[0].sources] == [10, 11]
    assert grouped[0].needs_manual_review is True
    assert grouped[1].external_id is None


def test_grouping_is_stable_and_supports_non_vesw_work_item_prefixes() -> None:
    requirements = (
        atomic(
            "REQ-a",
            "The interface shall report the state.",
            page=4,
            section="SYSREQ-42 - Interface state",
        ),
        atomic(
            "REQ-b",
            "The value has unit rpm.",
            page=5,
            section="SYSREQ-42 - Interface state",
        ),
    )

    first = group_logical_requirements(requirements)
    second = group_logical_requirements(requirements)

    assert first == second
    assert first[0].requirement_id == "SYSREQ-42"
    assert first[0].title == "Interface state"