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


def test_bullets_and_multi_digit_numbering_and_no_unnecessary_split() -> None:
    # bullets and multi-digit numbers should split
    doc = extracted_document_with("- alpha\n* beta\n10) ten\n11. eleven")
    items = normalize_requirements(doc)
    texts = [it.text for it in items]
    assert "alpha" in texts
    assert "beta" in texts
    assert "ten" in texts
    assert "eleven" in texts

    # paragraph with visual line breaks but no list delimiters should remain single
    long_para = "This is a wrapped paragraph\nthat should stay as one requirement."
    doc2 = extracted_document_with(long_para)
    items2 = normalize_requirements(doc2)
    assert len(items2) == 1


def test_heading_is_carried_and_not_emitted_as_requirement() -> None:
    # heading block followed by requirement should carry section
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            SourceRef(
                page=1,
                quote="Network:",
                section=None,
                table_index=None,
                bbox=(0, 0, 1, 1),
            ),
            SourceRef(
                page=1,
                quote="Wi-Fi must be available.",
                section=None,
                table_index=None,
                bbox=(0, 0, 1, 1),
            ),
        ),
        tables=(),
    )
    doc = ExtractedDocument(sha256="deadbeef", pages=(page,))
    items = normalize_requirements(doc)
    assert len(items) == 1
    assert items[0].sources[0].section == "Network"


def test_ambiguous_multi_sentence_includes_chinese_and_marks_manual() -> None:
    txt = "该设备必须运行。并在启动时进行校验。"
    doc = extracted_document_with(txt)
    items = normalize_requirements(doc)
    assert len(items) == 1
    assert items[0].needs_manual_review is True
