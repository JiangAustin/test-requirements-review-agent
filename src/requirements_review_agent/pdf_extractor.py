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
    # require Python >=3.12 path.is_relative_to semantics
    if not path.is_relative_to(workspace):
        raise ReviewException(
            ReviewError(
                code=PDF_OUTSIDE_WORKSPACE,
                message="PDF outside workspace",
                details={"path": str(path)},
            )
        )

    # Open and extract inside a context manager so file resources are always closed.
    try:
        with fitz.open(str(path)) as doc:
            if getattr(doc, "needs_pass", False):
                raise ReviewException(
                    ReviewError(
                        code=PDF_ENCRYPTED,
                        message="PDF is encrypted",
                        details={"path": str(path)},
                    )
                )

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
                                SourceRef(
                                    page=pnum,
                                    quote=text,
                                    section=None,
                                    table_index=None,
                                    bbox=bbox,
                                )
                            )

                page_text = "\n".join(texts)

                # tables
                tables = []
                try:
                    finder = page.find_tables()
                    tlist = list(getattr(finder, "tables", []))
                except Exception as exc:
                    raise ReviewException(
                        ReviewError(
                            code=PDF_DAMAGED,
                            message="table detection failed",
                            details={"error": repr(exc)},
                        )
                    ) from exc

                for ti, table in enumerate(tlist):
                    extracted_cells = True
                    try:
                        raw = table.extract()
                    except ReviewException:
                        raise
                    except Exception:
                        # If table metadata is available but cell extraction fails,
                        # preserve a minimal placeholder table row rather than
                        # failing the whole PDF. This keeps the table bbox/index
                        # and signals manual review is required.
                        raw = ((None,),)
                        extracted_cells = False
                    cells = tuple(tuple(cell for cell in row) for row in raw)
                    bbox = tuple(getattr(table, "bbox", (0.0, 0.0, 0.0, 0.0)))
                    needs_manual = any(cell is None for row in cells for cell in row)
                    header = getattr(table, "header", None)
                    header_names = tuple(getattr(header, "names", ()))
                    header_rows = int(
                        extracted_cells
                        and bool(cells)
                        and header is not None
                        and not bool(getattr(header, "external", True))
                        and bool(header_names)
                        and cells[0] == header_names
                    )
                    tables.append(
                        ExtractedTable(
                            page=pnum,
                            table_index=ti,
                            bbox=bbox,
                            cells=cells,
                            needs_manual_review=needs_manual,
                            header_rows=header_rows,
                        )
                    )

                # detect images
                images = page.get_images(full=True)
                # scanned if few chars and has images
                if len("".join(texts).strip()) < 20 and images:
                    scanned_detected = True

                pages.append(
                    ExtractedPage(
                        page=pnum,
                        text=page_text,
                        blocks=tuple(blocks),
                        tables=tuple(tables),
                    )
                )

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
    except ReviewException:
        # preserve explicit review exceptions
        raise
    except Exception as exc:
        # any other open/parse error => damaged
        raise ReviewException(
            ReviewError(
                code=PDF_DAMAGED,
                message="PDF damaged or unreadable",
                details={"error": repr(exc)},
            )
        ) from exc

    # old fallback extraction block removed; extraction is performed above
