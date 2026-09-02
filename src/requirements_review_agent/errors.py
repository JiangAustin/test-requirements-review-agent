from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ReviewError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    details: dict[str, object]


class ReviewException(Exception):
    def __init__(self, error: ReviewError) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")

    def __str__(self) -> str:
        return f"{self.error.code}: {self.error.message}"


PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_DAMAGED = "PDF_DAMAGED"
PDF_SCANNED = "PDF_SCANNED"
PDF_OUTSIDE_WORKSPACE = "PDF_OUTSIDE_WORKSPACE"
RULE_PACK_INVALID = "RULE_PACK_INVALID"
ANALYSIS_INVALID = "ANALYSIS_INVALID"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
REPORT_PARTIAL = "REPORT_PARTIAL"
