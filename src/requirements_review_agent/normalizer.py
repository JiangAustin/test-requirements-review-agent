from __future__ import annotations

import re
from hashlib import sha256

from .models import AtomicRequirement, ExtractedDocument, SourceRef


def _stable_id(source: SourceRef, normalized_text: str) -> str:
    identity = f"{source.page}|{source.section or ''}|{source.table_index}|{normalized_text}"
    return f"REQ-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"


def normalize_requirements(document: ExtractedDocument) -> tuple[AtomicRequirement, ...]:
    results: list[AtomicRequirement] = []

    for page in document.pages:
        # tables first: each non-empty data row => requirement
        for table in page.tables:
            for _r_idx, row in enumerate(table.cells):
                # skip fully empty rows
                if all((c is None or (isinstance(c, str) and not c.strip())) for c in row):
                    continue
                # join non-None with ' | '
                parts = [c for c in row if c is not None]
                text = " | ".join(parts)
                bbox_raw = tuple(table.bbox)
                bbox4 = (
                    float(bbox_raw[0]),
                    float(bbox_raw[1]),
                    float(bbox_raw[2]),
                    float(bbox_raw[3]),
                )
                src = SourceRef(
                    page=table.page,
                    quote=text or "",
                    section=None,
                    table_index=table.table_index,
                    bbox=bbox4,
                )
                rid = _stable_id(src, text)
                needs_manual = table.needs_manual_review or any(c is None for c in row)
                results.append(
                    AtomicRequirement(
                        requirement_id=rid,
                        text=text,
                        sources=(src,),
                        needs_manual_review=needs_manual,
                    )
                )

        # then paragraphs / blocks
        last_heading: str | None = None
        for block in page.blocks:
            txt = block.quote.strip()
            if not txt:
                continue

            # heading detection (conservative)
            # ends with ':' or '：' OR single short line without terminal punctuation
            single_line = "\n" not in txt
            ends_with_colon = txt.endswith(":") or txt.endswith("：")
            short_no_punct = (
                single_line
                and len(txt) <= 40
                and not re.search(r"[\.\?!。！？]$", txt)
            )

            if ends_with_colon or short_no_punct:
                last_heading = txt.rstrip(":：").strip()
                continue

            # split only when the block is clearly a list: all non-empty lines are delimited
            lines = [line.strip() for line in txt.splitlines() if line.strip()]
            if not lines:
                continue

            def is_bullet_line(s: str) -> bool:
                return bool(re.match(r"^\s*[-\*•]\s+", s))

            def is_numbered_line(s: str) -> bool:
                return bool(re.match(r"^\s*\d+[\.)]\s+", s))

            all_delimited = (
                len(lines) > 1
                and all(
                    is_bullet_line(line_text) or is_numbered_line(line_text)
                    for line_text in lines
                )
            )

            items: list[str]
            if all_delimited:
                items = []
                for line in lines:
                    m = re.match(r"^\s*[-\*•]\s+(.*)$", line)
                    if m:
                        items.append(m.group(1).strip())
                        continue
                    m = re.match(r"^\s*(\d+)[\.)]\s+(.*)$", line)
                    if m:
                        items.append(m.group(2).strip())
                        continue
                    items.append(line)
            else:
                # keep whole block intact (no deterministic delimiter)
                items = [txt]

            for item in items:
                # ambiguous if multiple sentences (support English + Chinese punctuation)
                sentences = re.split(r"[\.\?!。！？]+", item.strip())
                sentences = [s for s in sentences if s.strip()]
                multi_sent = len(sentences) > 1

                src = SourceRef(
                    page=block.page,
                    quote=item,
                    section=last_heading,
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
