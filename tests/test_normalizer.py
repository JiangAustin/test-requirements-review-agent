from __future__ import annotations

import pytest

from requirements_review_agent.models import (
    AtomicRequirement,
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    SourceRef,
)
from requirements_review_agent.normalizer import (
    detect_residual_document_noise,
    normalize_requirements,
    normalize_requirements_with_diagnostics,
)


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


def test_duplicate_text_blocks_and_table_rows_receive_unique_stable_ids() -> None:
    table = ExtractedTable(
        page=1,
        table_index=0,
        bbox=(40, 240, 500, 320),
        cells=(("Value",), ("Repeated",), ("Repeated",)),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Repeated requirement text.", (40, 100, 300, 120)),
            block(1, "Repeated requirement text.", (40, 100, 300, 120)),
        ),
        tables=(table,),
    )
    document = ExtractedDocument(sha256="deadbeef", pages=(page,))

    first = normalize_requirements(document)
    second = normalize_requirements(document)

    assert len({item.requirement_id for item in first}) == 4
    assert [item.requirement_id for item in first] == [
        item.requirement_id for item in second
    ]


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


def block(page: int, text: str, bbox: tuple[float, float, float, float]) -> SourceRef:
    return SourceRef(
        page=page,
        quote=text,
        section=None,
        table_index=None,
        bbox=bbox,
    )


def atomic_requirement(index: int, text: str) -> AtomicRequirement:
    source = block(1, text, (40, 100 + index, 500, 110 + index))
    return AtomicRequirement(
        requirement_id=f"REQ-{index:012d}",
        text=text,
        sources=(source,),
    )


def test_residual_noise_diagnostics_sample_first_fifty_and_limit_examples() -> None:
    requirements = tuple(
        atomic_requirement(
            index,
            (
                    f"{index + 1} Architecture overview . . . .{index + 4}"
                if index < 3
                else f"Page {index + 1} of 94 footer"
                if index < 6
                else f"Architecture description {index}."
                if index < 50
                else "Version Revision Approval Date"
            ),
        )
        for index in range(60)
    )

    result = detect_residual_document_noise(requirements)

    assert result.sample_size == 50
    assert result.suspected_count == 6
    assert result.reason_counts == {"page_number": 3, "table_of_contents": 3}
    assert result.example_requirement_ids == tuple(
        f"REQ-{index:012d}" for index in range(5)
    )


def test_filters_repeated_headers_footers_page_numbers_and_toc() -> None:
    pages = tuple(
        ExtractedPage(
            page=page_number,
            text="",
            blocks=(
                block(page_number, "Product Requirements Specification", (40, 20, 400, 35)),
                block(page_number, str(page_number), (280, 790, 300, 805)),
                block(page_number, "Device shall stop within 3 seconds.", (40, 200, 450, 230)),
            )
            if page_number > 1
            else (
                block(page_number, "Product Requirements Specification", (40, 20, 400, 35)),
                block(page_number, "Table of Contents", (40, 100, 300, 125)),
                block(page_number, "1. Safety requirements ........ 3", (40, 140, 400, 160)),
                block(page_number, str(page_number), (280, 790, 300, 805)),
            ),
            tables=(),
        )
        for page_number in range(1, 5)
    )
    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=pages)
    )

    assert {item.text for item in result.requirements} == {
        "Device shall stop within 3 seconds."
    }
    assert result.filtered_counts == {
        "page_number": 4,
        "repeated_margin": 3,
        "table_of_contents": 3,
    }


def test_filters_all_non_modal_blocks_on_singular_toc_page() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Table of Content", (40, 80, 300, 105)),
            block(1, "1 Introduction", (40, 130, 300, 150)),
            block(1, "4", (500, 130, 520, 150)),
            block(1, "2 Product overview 8", (40, 170, 520, 190)),
            block(1, "Device shall expose a content index.", (40, 220, 500, 245)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Device shall expose a content index."
    ]
    assert result.filtered_counts == {"table_of_contents": 4}


def test_filters_non_modal_table_rows_on_toc_page() -> None:
    table = ExtractedTable(
        page=1,
        table_index=0,
        bbox=(40, 120, 500, 220),
        cells=(
            ("Section", "Page"),
            ("1 Introduction", "4"),
            ("Device shall expose a content index.", "8"),
        ),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(block(1, "Table of Content", (40, 80, 300, 105)),),
        tables=(table,),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Device shall expose a content index."
    ]
    assert result.filtered_counts == {"table_of_contents": 3}


def test_detects_toc_continuation_page_from_spaced_dot_leaders() -> None:
    page = ExtractedPage(
        page=3,
        text="",
        blocks=(
            block(3, "4.2.5 Draw switch . . . . . . .52", (40, 100, 520, 120)),
            block(3, "4.2.6 Relay . . . . . . . . .54", (40, 140, 520, 160)),
            block(3, "5 Non-Functional Requirements . . . .84", (40, 180, 520, 200)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert result.requirements == ()
    assert result.filtered_counts == {"table_of_contents": 3}


def test_detects_toc_title_and_entries_combined_in_one_multiline_block() -> None:
    text = (
        "Table of Content\n"
        "1 Introduction . . . .4\n"
        "2 System Context . . . .5"
    )
    page = ExtractedPage(
        page=2,
        text=text,
        blocks=(block(2, text, (40, 90, 520, 180)),),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert result.requirements == ()
    assert result.filtered_counts == {"table_of_contents": 1}


def test_dotted_prose_without_section_numbers_does_not_mark_toc_page() -> None:
    texts = tuple(
        f"Retry operation after transient communication error {index} . . . . 3"
        for index in range(3)
    )
    page = ExtractedPage(
        page=20,
        text="\n".join(texts),
        blocks=tuple(
            block(20, text, (40, 100 + index * 40, 520, 120 + index * 40))
            for index, text in enumerate(texts)
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == list(texts)
    assert result.filtered_counts == {}


def test_numbered_parameter_table_does_not_mark_toc_page() -> None:
    table = ExtractedTable(
        page=20,
        table_index=0,
        bbox=(40, 100, 500, 260),
        cells=(
            ("Parameter", "Value"),
            ("1. Timeout", "30"),
            ("2. Retries", "3"),
            ("3. Channels", "4"),
        ),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(page=20, text="", blocks=(), tables=(table,), height=842)

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "1. Timeout | 30",
        "2. Retries | 3",
        "3. Channels | 4",
    ]
    assert result.filtered_counts == {}


def test_filters_approval_metadata_page_with_concatenated_pdf_text() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Ventilation PUMU Software Specification", (40, 90, 500, 110)),
            block(1, "Approved Versions", (40, 190, 300, 210)),
            block(1, "The current Revision 5209718 has been approved", (40, 220, 500, 240)),
            block(1, "VersionVersion CommentPolarion RevisionApproval Date", (40, 270, 520, 290)),
            block(1, "1.2Initial creation50992362026-07-24 03:22", (40, 300, 520, 320)),
            block(1, "Document Signatures", (40, 350, 300, 370)),
            block(1, "Cioflica Paul (BSH GDE-SVVS)Signed2026-09-02 15:53", (40, 390, 520, 410)),
            block(1, "Revision must remain visible in diagnostics.", (40, 450, 500, 470)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Revision must remain visible in diagnostics."
    ]
    assert result.filtered_counts == {"document_metadata": 7}


def test_detects_metadata_markers_combined_in_one_multiline_block() -> None:
    text = "Approved Versions\nDocument Signatures\n1.2Initial creation50992362026-07-24"
    page = ExtractedPage(
        page=1,
        text=text,
        blocks=(block(1, text, (40, 190, 520, 320)),),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert result.requirements == ()
    assert result.filtered_counts == {"document_metadata": 1}


def test_detects_metadata_markers_split_across_adjacent_blocks() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Approved", (40, 190, 150, 210)),
            block(1, "Versions", (160, 190, 260, 210)),
            block(1, "Document", (40, 230, 150, 250)),
            block(1, "Signatures", (160, 230, 280, 250)),
            block(1, "1.2Initial creation50992362026-07-24", (40, 270, 520, 290)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert result.requirements == ()
    assert result.filtered_counts == {"document_metadata": 5}


def test_mixed_metadata_block_keeps_only_modal_line_for_manual_review() -> None:
    text = (
        "Approved Versions\n"
        "Document Signatures\n"
        "The controller shall expose the approved revision."
    )
    page = ExtractedPage(
        page=1,
        text=text,
        blocks=(block(1, text, (40, 190, 520, 320)),),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "The controller shall expose the approved revision."
    ]
    assert result.requirements[0].needs_manual_review is True
    assert result.filtered_counts == {"document_metadata": 2}


def test_filters_combined_page_footer_only_in_margin() -> None:
    footer = "Page 1 of 94Software System Documentation - VE2026-09-03 10:40"
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, footer, (40, 810, 520, 830)),
            block(1, footer, (40, 300, 520, 320)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [footer]
    assert result.filtered_counts == {"page_number": 1}


@pytest.mark.parametrize(
    "text",
    [
        "The current Revision 5209718 has been approved",
        "Cioflica Paul (VE)Signed2026-09-02 15:53",
        "Initial creation revision 5209718 2026-09-02",
        "Version Version Comment Polarion Revision Approval Date",
        "Approved Versions",
        "Revision History",
        "Approval History",
    ],
)
def test_filters_unstructured_approval_and_revision_metadata(text: str) -> None:
    result = normalize_requirements_with_diagnostics(extracted_document_with(text))

    assert result.requirements == ()
    assert result.filtered_counts == {"document_metadata": 1}


@pytest.mark.parametrize(
    "text",
    [
        "The software shall report the approved configuration version.",
        "The device must enter the signed firmware update state.",
    ],
)
def test_keeps_approval_language_when_text_is_a_requirement(text: str) -> None:
    result = normalize_requirements_with_diagnostics(extracted_document_with(text))

    assert [item.text for item in result.requirements] == [text]
    assert result.filtered_counts == {}


def test_filters_document_approval_metadata_but_keeps_real_requirement() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Document ID: PRS-1234", (40, 80, 300, 100)),
            block(1, "Revision: 1.2", (40, 110, 300, 130)),
            block(1, "Approved by: Jane Doe", (40, 140, 300, 160)),
            block(1, "The controller must retain the revision identifier.", (40, 220, 500, 245)),
        ),
        tables=(),
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "The controller must retain the revision identifier."
    ]
    assert result.filtered_counts == {"document_metadata": 3}


def test_keeps_metadata_like_text_when_it_contains_requirement_language() -> None:
    document = extracted_document_with("Revision must be visible in the diagnostic report.")

    result = normalize_requirements_with_diagnostics(document)

    assert [item.text for item in result.requirements] == [
        "Revision must be visible in the diagnostic report."
    ]
    assert result.filtered_counts == {}


def test_keeps_status_sentence_and_colon_terminated_modal_requirement() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Status update is pending.", (40, 100, 300, 120)),
            block(1, "The device must:", (40, 140, 300, 160)),
        ),
        tables=(),
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Status update is pending.",
        "The device must:",
    ]
    assert result.filtered_counts == {}


def test_keeps_modal_toc_like_text_and_body_number() -> None:
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Device shall stop ........ 4", (40, 150, 400, 170)),
            block(1, "30", (40, 300, 80, 320)),
        ),
        tables=(),
        height=842,
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Device shall stop ........ 4",
        "30",
    ]


def test_filters_metadata_without_colon_and_drifting_a4_footer() -> None:
    pages = tuple(
        ExtractedPage(
            page=page_number,
            text="",
            blocks=(
                block(page_number, "Document ID PRS-1234", (40, 100, 300, 120)),
                block(
                    page_number,
                    "Confidential",
                    (40, 805 + (page_number * 3), 200, 820 + (page_number * 3)),
                ),
            ),
            tables=(),
            height=842,
        )
        for page_number in range(1, 4)
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=pages)
    )

    assert result.requirements == ()
    assert result.filtered_counts == {
        "document_metadata": 3,
        "repeated_margin": 3,
    }


def test_filters_text_block_duplicated_inside_extracted_table() -> None:
    table = ExtractedTable(
        page=1,
        table_index=0,
        bbox=(40, 100, 500, 200),
        cells=(("Requirement", "Value"), ("Timeout", "30 s")),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(
            block(1, "Requirement Value Timeout 30 s", (45, 105, 495, 195)),
            block(1, "Device shall report a timeout event.", (40, 240, 500, 265)),
        ),
        tables=(table,),
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert {item.text for item in result.requirements} == {
        "Timeout | 30 s",
        "Device shall report a timeout event.",
    }
    assert result.filtered_counts == {"table_overlap": 1}


def test_filters_revision_history_table_but_keeps_parameter_table() -> None:
    revision_table = ExtractedTable(
        page=1,
        table_index=0,
        bbox=(40, 100, 500, 180),
        cells=(
            ("Revision", "Date", "Author", "Description"),
            ("1.0", "2026-09-03", "Jane Doe", "Initial release"),
        ),
        needs_manual_review=False,
        header_rows=1,
    )
    parameter_table = ExtractedTable(
        page=1,
        table_index=1,
        bbox=(40, 220, 500, 300),
        cells=(("Parameter", "Value"), ("Timeout", "30 s")),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(
        page=1,
        text="",
        blocks=(),
        tables=(revision_table, parameter_table),
    )

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == ["Timeout | 30 s"]
    assert result.filtered_counts == {"document_metadata": 1}


def test_filters_approval_table_but_keeps_rows_with_requirement_language() -> None:
    table = ExtractedTable(
        page=1,
        table_index=0,
        bbox=(40, 100, 500, 220),
        cells=(
            ("Approval", "Date", "Approver", "Signature"),
            ("Accepted", "2026-09-03", "Jane Doe", "JD"),
            ("Constraint", "", "", "Approval must be recorded."),
        ),
        needs_manual_review=False,
        header_rows=1,
    )
    page = ExtractedPage(page=1, text="", blocks=(), tables=(table,))

    result = normalize_requirements_with_diagnostics(
        ExtractedDocument(sha256="deadbeef", pages=(page,))
    )

    assert [item.text for item in result.requirements] == [
        "Constraint |  |  | Approval must be recorded."
    ]
    assert result.filtered_counts == {"document_metadata": 1}
