from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from requirements_review_agent.errors import ReviewException
from requirements_review_agent.normalizer import normalize_requirements
from requirements_review_agent.pdf_extractor import extract_pdf
from tests.fixtures.build_pdfs import (
    build_damaged_pdf,
    build_encrypted_pdf,
    build_image_only_pdf,
    build_text_table_pdf,
)


def test_extracts_text_table_and_one_based_sources(tmp_path: Path) -> None:
    pdf = build_text_table_pdf(tmp_path / "mixed.pdf")
    result = extract_pdf(pdf, tmp_path)
    assert result.pages[0].page == 1
    assert "Wi-Fi" in result.pages[0].text
    # table cells present
    assert result.pages[0].tables
    first_table = result.pages[0].tables[0]
    # convert to lists for easier assertion
    cells = [[c for c in row] for row in first_table.cells]
    assert ["Timeout", "30 s"] in cells
    assert first_table.header_rows == 1
    requirements = normalize_requirements(result)
    assert "Param | Value" not in {item.text for item in requirements}
    assert "Timeout | 30 s" in {item.text for item in requirements}
    # blocks should include bbox and document should have sha256
    assert result.pages[0].blocks
    assert isinstance(result.pages[0].blocks[0].bbox, tuple)
    assert len(result.sha256) == 64


def test_real_multi_page_pdf_filters_document_noise(tmp_path: Path) -> None:
    import fitz as _fitz

    path = tmp_path / "document-noise.pdf"
    document = _fitz.open()
    for page_number in range(1, 5):
        page = document.new_page()
        page.insert_text((50, 30), "Product Requirements Specification", fontsize=9)
        page.insert_text((290, 790), str(page_number), fontsize=9)
        if page_number == 1:
            page.insert_text((50, 100), "Table of Contents", fontsize=12)
            page.insert_text((50, 135), "1. Safety requirements ........ 3", fontsize=10)
            page.insert_text((50, 180), "Approved by: Jane Doe", fontsize=10)
        else:
            page.insert_text(
                (50, 180),
                f"Device shall stop within {page_number} seconds.",
                fontsize=11,
            )
    document.save(path)
    document.close()

    result = normalize_requirements(extract_pdf(path, tmp_path))

    assert {item.text for item in result} == {
        "Device shall stop within 2 seconds.",
        "Device shall stop within 3 seconds.",
        "Device shall stop within 4 seconds.",
    }


def test_rejects_scanned_or_empty_page(tmp_path: Path) -> None:
    pdf = build_image_only_pdf(tmp_path / "scan.pdf")
    with pytest.raises(ReviewException, match="PDF_SCANNED"):
        extract_pdf(pdf, tmp_path)


def test_handles_encrypted_and_damaged(tmp_path: Path) -> None:
    enc = build_encrypted_pdf(tmp_path / "enc.pdf")
    with pytest.raises(ReviewException, match="PDF_ENCRYPTED"):
        extract_pdf(enc, tmp_path)

    dmg = build_damaged_pdf(tmp_path / "damaged.pdf")
    with pytest.raises(ReviewException, match="PDF_DAMAGED"):
        extract_pdf(dmg, tmp_path)


def test_rejects_outside_workspace_sibling_prefix(tmp_path: Path) -> None:
    # create workspace "foo" and a sibling "foobar" to ensure prefix matching fails
    ws = tmp_path / "foo"
    sb = tmp_path / "foobar"
    ws.mkdir()
    sb.mkdir()
    # create a valid PDF in sibling
    pdf = build_text_table_pdf(sb / "mixed.pdf")
    # passing workspace as ws should reject
    with pytest.raises(ReviewException, match="PDF_OUTSIDE_WORKSPACE"):
        extract_pdf(pdf, ws)


def test_find_tables_failure_maps_to_damaged(tmp_path: Path, monkeypatch) -> None:
    pdf = build_text_table_pdf(tmp_path / "mixed.pdf")
    import fitz as _fitz

    def _raise_find(self):
        raise RuntimeError("finder failed")

    monkeypatch.setattr(_fitz.Page, "find_tables", _raise_find)
    with pytest.raises(ReviewException, match="PDF_DAMAGED"):
        extract_pdf(pdf, tmp_path)


def test_table_extract_exception_preserved(tmp_path: Path, monkeypatch) -> None:
    """When table.extract() raises, extraction should succeed but the table
    must be preserved as a placeholder ((None,),) and marked needs_manual_review.
    Normalizer must not produce an empty AtomicRequirement for that placeholder.
    """
    pdf = build_text_table_pdf(tmp_path / "mixed.pdf")
    import fitz as _fitz

    from requirements_review_agent.normalizer import normalize_requirements

    orig_find = _fitz.Page.find_tables

    def _wrapper_find(self):
        finder = orig_find(self)
        for t in list(getattr(finder, "tables", [])):
            def _raise_extract():
                raise RuntimeError("extract failed")

            # bind to instance (best-effort; some table objects are C-extension types)
            with contextlib.suppress(Exception):
                t.extract = _raise_extract

        return finder

    monkeypatch.setattr(_fitz.Page, "find_tables", _wrapper_find)

    result = extract_pdf(pdf, tmp_path)
    assert result.pages[0].tables
    t = result.pages[0].tables[0]
    assert t.page == 1
    assert t.table_index == 0
    assert isinstance(t.bbox, tuple)
    assert t.cells == ((None,),)
    assert t.needs_manual_review is True

    # Normalizer should not generate an empty AtomicRequirement for the placeholder
    items = normalize_requirements(result)
    # ensure none of the normalized items claim the table_index 0 from page 1
    assert not any(
        it.sources[0].page == 1 and it.sources[0].table_index == 0 for it in items
    )
