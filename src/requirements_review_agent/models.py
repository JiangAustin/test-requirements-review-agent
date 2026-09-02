from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ReviewError


class CheckStatus(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_CONFIRMATION = "needs_confirmation"


class Impact(StrEnum):
    MANUAL = "manual"
    AUTOMATION = "automation"
    BOTH = "both"


class Severity(StrEnum):
    BLOCKING = "blocking"
    IMPORTANT = "important"
    NORMAL = "normal"


class FindingType(StrEnum):
    FACT = "fact"
    SUGGESTION = "suggestion"


class ProviderMode(StrEnum):
    COPILOT = "copilot"
    COMPANY_API = "company_api"
    LOCAL = "local"


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(..., ge=1)
    quote: str = Field(..., min_length=1)
    section: str | None = None
    table_index: int | None = Field(default=None, ge=0)
    bbox: tuple[float, float, float, float] | None = None


class ExtractedTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(..., ge=1)
    table_index: int = Field(..., ge=0)
    bbox: tuple[float, float, float, float]
    cells: tuple[tuple[str | None, ...], ...]
    needs_manual_review: bool


class ExtractedPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(..., ge=1)
    text: str
    blocks: tuple[SourceRef, ...]
    tables: tuple[ExtractedTable, ...]


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str
    pages: tuple[ExtractedPage, ...]


class AtomicRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    text: str
    sources: tuple[SourceRef, ...]
    needs_manual_review: bool = False


class RuleCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    question: str
    weight: int = Field(..., gt=0)
    impact: Impact
    scenario_category: str | None
    always: bool
    keywords: tuple[str, ...]


class ApplicableRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    question: str
    weight: int = Field(..., gt=0)
    impact: Impact
    scenario_category: str | None
    always: bool
    keywords: tuple[str, ...]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    status: CheckStatus
    impact: Impact
    severity: Severity
    finding_type: FindingType
    evidence: tuple[SourceRef, ...]
    rationale: str | None = None
    question: str | None = None
    confidence: float = Field(..., ge=0, le=1)

    @model_validator(mode="after")
    def require_fact_evidence(self) -> Self:
        if self.finding_type is FindingType.FACT and not self.evidence:
            raise ValueError("fact findings must include evidence")
        return self


class ScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    description: str
    covered: bool
    evidence: tuple[SourceRef, ...]

    @model_validator(mode="after")
    def require_covered_evidence(self) -> Self:
        if self.covered and not self.evidence:
            raise ValueError("covered scenarios must include evidence")
        return self


class RequirementAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    checks: tuple[CheckResult, ...]
    scenarios: tuple[ScenarioResult, ...]


class AnalysisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    requirements: tuple[RequirementAnalysis, ...]


class RequirementScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    testability: float = Field(..., ge=0, le=100)
    scenario_coverage: float = Field(..., ge=0, le=100)


class AggregateScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    testability: float = Field(..., ge=0, le=100)
    scenario_coverage: float = Field(..., ge=0, le=100)


class RequirementReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: AtomicRequirement
    analysis: RequirementAnalysis
    score: RequirementScore


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    pdf_hash: str
    rule_version: str
    model_mode: ProviderMode
    schema_version: str
    stage: str
    created_at: str


class ReportArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    json_path: Path = Field(alias="json")
    markdown: Path
    docx: Path | None
    status: Literal["complete", "partial"]

    @property
    def json(self) -> Path:  # type: ignore[override]
        return self.json_path


class PreparedReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    provider_mode: ProviderMode
    data_destination: str
    requirement_count: int
    warnings: tuple[str, ...]
    batch_count: int


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    stage: str
    requirement_count: int
    analyzed_count: int
    warnings: tuple[str, ...]
    artifacts: ReportArtifacts | None


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    run_id: str | None
    generated_at: str
    provider_mode: ProviderMode
    model_name: str | None
    rule_version: str
    requirements: tuple[RequirementReview, ...]
    aggregate: AggregateScore
    failures: tuple[ReviewError, ...]
