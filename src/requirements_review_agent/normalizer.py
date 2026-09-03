from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from .models import AtomicRequirement, ExtractedDocument, SourceRef

FilterReason = Literal[
    "document_metadata",
    "page_number",
    "repeated_margin",
    "table_of_contents",
    "table_overlap",
]

_PAGE_NUMBER_RE = re.compile(r"^(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE)
_TOC_ENTRY_RE = re.compile(r"^.+?\s*\.{2,}\s*\d+\s*$")
_TOC_TITLE_RE = re.compile(r"^(?:table\s+of\s+contents|contents)$", re.IGNORECASE)
_DOCUMENT_METADATA_RE = re.compile(
    r"^(?:(?:document\s+(?:id|number|no\.?|title)|revision|version|status|"
    r"approved\s+by|prepared\s+by|reviewed\s+by|author|owner|date)\s*[:：]\s*\S.*|"
    r"document\s+(?:id|number|no\.?)\s+\S*\d\S*|"
    r"(?:revision|version)\s+v?\d[\w.-]*|"
    r"status\s+(?:approved|draft|final|released)|"
    r"(?:approved|prepared|reviewed)\s+by\s+\S.+|"
    r"date\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2})$",
    re.IGNORECASE,
)
_REQUIREMENT_MODAL_RE = re.compile(
    r"\b(?:shall|must|should|required|needs?\s+to)\b|必须|应当|应该|需要|不得",
    re.IGNORECASE,
)
_METADATA_TABLE_HEADERS = frozenset(
    {
        "approved by",
        "approval",
        "approver",
        "author",
        "date",
        "description",
        "prepared by",
        "revision",
        "reviewed by",
        "signature",
        "status",
        "version",
    }
)


@dataclass(frozen=True)
class NormalizationResult:
    requirements: tuple[AtomicRequirement, ...]
    filtered_counts: dict[FilterReason, int]


def _stable_id(source: SourceRef, normalized_text: str) -> str:
    identity = f"{source.page}|{source.section or ''}|{source.table_index}|{normalized_text}"
    return f"REQ-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"


def _overlap_ratio(
    block_bbox: tuple[float, float, float, float] | None,
    table_bbox: tuple[float, float, float, float],
) -> float:
    if block_bbox is None:
        return 0.0
    left = max(block_bbox[0], table_bbox[0])
    top = max(block_bbox[1], table_bbox[1])
    right = min(block_bbox[2], table_bbox[2])
    bottom = min(block_bbox[3], table_bbox[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    block_area = max(0.0, block_bbox[2] - block_bbox[0]) * max(
        0.0, block_bbox[3] - block_bbox[1]
    )
    return intersection / block_area if block_area else 0.0


def _margin_zone(y: float, page_height: float | None) -> Literal["top", "bottom"] | None:
    height = page_height or 792.0
    if y <= height * 0.1:
        return "top"
    if y >= height * 0.88:
        return "bottom"
    return None


def _repeated_margin_keys(document: ExtractedDocument) -> set[tuple[str, str]]:
    positions: dict[tuple[str, str], list[float]] = {}
    for page in document.pages:
        seen_on_page: set[tuple[str, str]] = set()
        for block in page.blocks:
            text = " ".join(block.quote.split())
            zone = _margin_zone(block.bbox[1], page.height) if block.bbox is not None else None
            if (
                block.bbox is None
                or len(text) > 120
                or _REQUIREMENT_MODAL_RE.search(text)
                or zone is None
            ):
                continue
            key = (text.casefold(), zone)
            if key not in seen_on_page:
                positions.setdefault(key, []).append(block.bbox[1] / (page.height or 792.0))
                seen_on_page.add(key)
    repeated: set[tuple[str, str]] = set()
    for repeated_key, values in positions.items():
        sorted_values = sorted(values)
        if any(
            sorted_values[index + 2] - sorted_values[index] <= 0.03
            for index in range(len(sorted_values) - 2)
        ):
            repeated.add(repeated_key)
    return repeated


def _block_filter_reason(
    block: SourceRef,
    table_bboxes: tuple[tuple[float, float, float, float], ...],
    repeated_margin_keys: set[tuple[str, str]],
    page_height: float | None,
) -> FilterReason | None:
    text = " ".join(block.quote.split())
    modal = _REQUIREMENT_MODAL_RE.search(text)
    zone = _margin_zone(block.bbox[1], page_height) if block.bbox is not None else None
    if _PAGE_NUMBER_RE.fullmatch(text) and zone is not None:
        return "page_number"
    if (_TOC_TITLE_RE.fullmatch(text) or _TOC_ENTRY_RE.fullmatch(text)) and not modal:
        return "table_of_contents"
    if _DOCUMENT_METADATA_RE.match(text) and not modal:
        return "document_metadata"
    if any(_overlap_ratio(block.bbox, table_bbox) >= 0.8 for table_bbox in table_bboxes):
        return "table_overlap"
    if zone is not None:
        margin_key = (text.casefold(), zone)
        if margin_key in repeated_margin_keys:
            return "repeated_margin"
    return None


def _is_metadata_table(table_cells: tuple[tuple[str | None, ...], ...], header_rows: int) -> bool:
    if header_rows < 1 or not table_cells:
        return False
    headers = {
        " ".join(cell.casefold().split())
        for row in table_cells[:header_rows]
        for cell in row
        if cell is not None and cell.strip()
    }
    matched = headers & _METADATA_TABLE_HEADERS
    revision_history = (
        "date" in matched
        and bool(matched & {"revision", "version"})
        and bool(matched & {"author", "description", "status"})
    )
    approval_history = (
        "date" in matched
        and bool(matched & {"approval", "approved by"})
        and len(matched & {"approver", "approved by", "signature", "status"}) >= 2
    )
    return revision_history or approval_history


def normalize_requirements_with_diagnostics(
    document: ExtractedDocument,
) -> NormalizationResult:
    results: list[AtomicRequirement] = []
    filtered_counts: Counter[FilterReason] = Counter()
    repeated_margin_keys = _repeated_margin_keys(document)

    for page in document.pages:
        # tables first: each non-empty data row => requirement
        for table in page.tables:
            metadata_table = _is_metadata_table(table.cells, table.header_rows)
            for _r_idx, row in enumerate(table.cells[table.header_rows :]):
                # skip fully empty rows
                if all((c is None or (isinstance(c, str) and not c.strip())) for c in row):
                    continue
                # join non-None with ' | '
                parts = [c for c in row if c is not None]
                text = " | ".join(parts)
                if metadata_table and not _REQUIREMENT_MODAL_RE.search(text):
                    filtered_counts["document_metadata"] += 1
                    continue
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
        table_bboxes = tuple(table.bbox for table in page.tables)
        for block in page.blocks:
            txt = block.quote.strip()
            if not txt:
                continue
            filter_reason = _block_filter_reason(
                block, table_bboxes, repeated_margin_keys, page.height
            )
            if filter_reason is not None:
                filtered_counts[filter_reason] += 1
                continue

            # heading detection (conservative)
            # ends with ':' or '：' OR single short line without terminal punctuation
            single_line = "\n" not in txt
            ends_with_colon = txt.endswith(":") or txt.endswith("：")
            short_no_punct = (
                single_line
                and len(txt) <= 40
                and not re.search(r"[\.\?!。！？]$", txt)
                and not _REQUIREMENT_MODAL_RE.search(txt)
                and not txt.isdecimal()
            )

            if (ends_with_colon or short_no_punct) and not _REQUIREMENT_MODAL_RE.search(txt):
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

    return NormalizationResult(
        requirements=tuple(results),
        filtered_counts=dict(sorted(filtered_counts.items())),
    )


def normalize_requirements(document: ExtractedDocument) -> tuple[AtomicRequirement, ...]:
    return normalize_requirements_with_diagnostics(document).requirements
