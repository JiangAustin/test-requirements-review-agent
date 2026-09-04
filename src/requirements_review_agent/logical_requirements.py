from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import AtomicRequirement, LogicalRequirement, SourceRef

_WORK_ITEM_RE = re.compile(
    r"^(?P<id>[A-Z][A-Z0-9_]*-\d+)\s+-\s+(?P<title>\S.*)$"
)
_NORMATIVE_RE = re.compile(
    r"\b(?:shall|must|should|required|needs?\s+to)\b|必须|应当|应该|需要|不得",
    re.IGNORECASE,
)


@dataclass
class _LogicalGroup:
    requirement_id: str
    title: str
    external_id: str
    texts: list[str] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    needs_manual_review: bool = False


def group_logical_requirements(
    requirements: tuple[AtomicRequirement, ...],
) -> tuple[LogicalRequirement, ...]:
    ordered: list[LogicalRequirement | _LogicalGroup] = []
    groups: dict[str, _LogicalGroup] = {}

    for requirement in requirements:
        section = requirement.sources[0].section if requirement.sources else None
        match = _WORK_ITEM_RE.fullmatch(section or "")
        if match is None:
            if _NORMATIVE_RE.search(requirement.text) is None:
                continue
            ordered.append(
                LogicalRequirement(
                    requirement_id=requirement.requirement_id,
                    text=requirement.text,
                    sources=requirement.sources,
                    needs_manual_review=requirement.needs_manual_review,
                )
            )
            continue

        external_id = match.group("id")
        group = groups.get(external_id)
        if group is None:
            group = _LogicalGroup(
                requirement_id=external_id,
                title=match.group("title"),
                external_id=external_id,
            )
            groups[external_id] = group
            ordered.append(group)
        elif group.title != match.group("title"):
            group.needs_manual_review = True

        group.texts.append(requirement.text)
        group.sources.extend(requirement.sources)
        group.needs_manual_review = (
            group.needs_manual_review or requirement.needs_manual_review
        )

    results: list[LogicalRequirement] = []
    for item in ordered:
        if isinstance(item, LogicalRequirement):
            results.append(item)
            continue
        results.append(
            LogicalRequirement(
                requirement_id=item.requirement_id,
                title=item.title,
                text="\n".join(item.texts),
                sources=tuple(item.sources),
                external_id=item.external_id,
                needs_manual_review=item.needs_manual_review,
            )
        )
    return tuple(results)


__all__ = ["group_logical_requirements"]