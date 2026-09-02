from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ReviewError:
    code: str
    message: str
    details: Dict[str, Any]


class ReviewException(Exception):
    def __init__(self, error: ReviewError) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")

    def __str__(self) -> str:
        return f"{self.error.code}: {self.error.message}"


# Standardized exception codes
PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_DAMAGED = "PDF_DAMAGED"
PDF_SCANNED = "PDF_SCANNED"
PDF_OUTSIDE_WORKSPACE = "PDF_OUTSIDE_WORKSPACE"
RULE_PACK_INVALID = "RULE_PACK_INVALID"
ANALYSIS_INVALID = "ANALYSIS_INVALID"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
REPORT_PARTIAL = "REPORT_PARTIAL"
