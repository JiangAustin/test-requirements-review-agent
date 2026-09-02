from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import fitz  # type: ignore

from .errors import (
    PDF_DAMAGED,
    PDF_ENCRYPTED,
    PDF_OUTSIDE_WORKSPACE,
    PDF_SCANNED,
    ReviewError,
    ReviewException,
)
from .models import ExtractedDocument, ExtractedPage, ExtractedTable, SourceRef


def extract_pdf(path: Path, workspace: Path) -> ExtractedDocument:
    path = path.resolve()
    workspace = workspace.resolve()
    try:
        if not path.is_relative_to(workspace):
            raise ReviewException(
                ReviewError(
                    code=PDF_OUTSIDE_WORKSPACE,
                    message="PDF outside workspace",
                    details={"path": str(path)},
                )
            )
    except Exception:
        # fallback for older Python versions
        if str(path).find(str(workspace)) != 0:
            raise ReviewException(
                ReviewError(
                    code=PDF_OUTSIDE_WORKSPACE,
                    message="PDF outside workspace",
                    details={"path": str(path)},
                )
            ) from None

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # damaged or unreadable
        raise ReviewException(
            ReviewError(
                code=PDF_DAMAGED,
                message="PDF damaged or unreadable",
                details={"error": repr(exc)},
            )
        ) from exc

    try:
        if getattr(doc, "needs_pass", False):
            raise ReviewException(
                ReviewError(
                    code=PDF_ENCRYPTED,
                    message="PDF is encrypted",
                    details={"path": str(path)},
                )
            )
    finally:
        # keep doc open for extraction
        pass

    pages = []
    scanned_detected = False
    for page in doc:
        pnum = page.number + 1
        pdict = page.get_text("dict", sort=True)
        blocks = []
        texts = []
        for block in pdict.get("blocks", []):
            if block.get("type") == 0:
                spans = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        txt = span.get("text", "")
                        if txt and txt.strip():
                            spans.append(txt)
                if spans:
                    text = "".join(spans)
                    texts.append(text)
                    bbox = tuple(block.get("bbox", [0, 0, 0, 0]))
                    blocks.append(
                        SourceRef(page=pnum, quote=text, section=None, table_index=None, bbox=bbox)
                    )

        page_text = "\n".join(texts)

        # tables
        tables = []
        try:
            finder = page.find_tables()
            tlist = list(getattr(finder, "tables", []))
        except Exception:
            tlist = []

        for ti, table in enumerate(tlist):
            try:
                raw = table.extract()
                cells = tuple(tuple(cell for cell in row) for row in raw)
            except Exception:
                cells = tuple()
            bbox = tuple(getattr(table, "bbox", (0.0, 0.0, 0.0, 0.0)))
            needs_manual = any(cell is None for row in cells for cell in row)
            tables.append(
                ExtractedTable(
                    page=pnum,
                    table_index=ti,
                    bbox=bbox,
                    cells=cells,
                    needs_manual_review=needs_manual,
                )
            )

        # detect images
        images = page.get_images(full=True)
        # scanned if few chars and has images
        if len("".join(texts).strip()) < 20 and images:
            scanned_detected = True

        pages.append(
            ExtractedPage(page=pnum, text=page_text, blocks=tuple(blocks), tables=tuple(tables))
        )

    doc.close()

    if scanned_detected:
        raise ReviewException(
            ReviewError(
                code=PDF_SCANNED,
                message="PDF appears scanned or image-only page present",
                details={"path": str(path)},
            )
        )

    sha = sha256(path.read_bytes()).hexdigest()
    return ExtractedDocument(sha256=sha, pages=tuple(pages))
