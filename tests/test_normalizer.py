from __future__ import annotations

from requirements_review_agent.models import (
    ExtractedDocument,
    ExtractedPage,
    SourceRef,
)
from requirements_review_agent.normalizer import normalize_requirements


def extracted_document_with(text: str) -> ExtractedDocument:
    page = ExtractedPage(
        page=1,
        text=text,
        blocks=(
            SourceRef(
                page=1,
                quote=text,
                section=None,
                table_index=None,
                bbox=(0.0, 0.0, 1.0, 1.0),
            ),
        ),
        tables=(),
    )
    return ExtractedDocument(sha256="deadbeef", pages=(page,))


def test_normalizer_splits_numbered_items_and_has_stable_ids() -> None:
    document = extracted_document_with(
        "1. Hood shall enter Boost mode.\n2. App displays filter status."
    )
    first = normalize_requirements(document)
    second = normalize_requirements(document)
    assert [item.requirement_id for item in first] == [item.requirement_id for item in second]
    assert len(first) == 2
    assert first[0].sources[0].page == 1
