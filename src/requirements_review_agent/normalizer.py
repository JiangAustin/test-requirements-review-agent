from __future__ import annotations

from hashlib import sha256
from typing import cast

from .models import AtomicRequirement, ExtractedDocument, SourceRef


def _stable_id(source: SourceRef, normalized_text: str) -> str:
    identity = f"{source.page}|{source.section or ''}|{source.table_index}|{normalized_text}"
    return f"REQ-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"


def normalize_requirements(document: ExtractedDocument) -> tuple[AtomicRequirement, ...]:
    results: list[AtomicRequirement] = []

    for page in document.pages:
        # tables first
        for table in page.tables:
            for _r_idx, row in enumerate(table.cells):
                # join non-None with ' | '
                parts = [c for c in row if c is not None]
                text = " | ".join(parts)
                bbox4 = cast(tuple[float, float, float, float], tuple(table.bbox))
                src = SourceRef(
                    page=table.page,
                    quote=text or "",
                    section=None,
                    table_index=table.table_index,
                    bbox=bbox4,
                )
                rid = _stable_id(src, text)
                needs_manual = table.needs_manual_review
                results.append(
                    AtomicRequirement(
                        requirement_id=rid,
                        text=text,
                        sources=(src,),
                        needs_manual_review=needs_manual,
                    )
                )

        # then paragraphs / blocks
        for block in page.blocks:
            txt = block.quote.strip()
            if not txt:
                continue
            # numbered/bulleted lines
            lines = [line_var.strip() for line_var in txt.splitlines() if line_var.strip()]
            split_items: list[str] = []
            for line in lines:
                if line.startswith("-") or line.startswith("*") or line.startswith("•"):
                    split_items.append(line.lstrip("-*• "))
                elif line and line[0].isdigit() and line[1:3] in (".", ") "):
                    # handle '1.' or '1)'
                    # crude split for leading digits
                    import re

                    m = re.match(r"^\d+[\.)]\s*(.*)$", line)
                    if m:
                        split_items.append(m.group(1))
                    else:
                        split_items.append(line)
                else:
                    split_items.append(line)

            if len(split_items) > 1:
                for item in split_items:
                    src = SourceRef(
                        page=block.page,
                        quote=item,
                        section=block.section,
                        table_index=None,
                        bbox=block.bbox,
                    )
                    rid = _stable_id(src, item)
                    # assume single-line items are fine
                    needs_manual = False
                    results.append(
                        AtomicRequirement(
                            requirement_id=rid,
                            text=item,
                            sources=(src,),
                            needs_manual_review=needs_manual,
                        )
                    )
            else:
                item = split_items[0]
                # ambiguous if multiple sentences
                multi_sent = sum(item.count(p) for p in (". ", "? ", "! ")) >= 1
                src = SourceRef(
                    page=block.page,
                    quote=item,
                    section=block.section,
                    table_index=None,
                    bbox=block.bbox,
                )
                rid = _stable_id(src, item)
                results.append(
                    AtomicRequirement(
                        requirement_id=rid,
                        text=item,
                        sources=(src,),
                        needs_manual_review=multi_sent,
                    )
                )

    return tuple(results)
