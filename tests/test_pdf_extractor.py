from __future__ import annotations

from pathlib import Path

import pytest

from requirements_review_agent.errors import ReviewException
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
    # blocks should include bbox and document should have sha256
    assert result.pages[0].blocks
    assert isinstance(result.pages[0].blocks[0].bbox, tuple)
    assert len(result.sha256) == 64


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
