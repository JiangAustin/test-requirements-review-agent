from __future__ import annotations

from enum import StrEnum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, constr


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
    section: Optional[str] = None
    table_index: Optional[int] = Field(default=None, ge=0)
    bbox: Optional[Tuple[float, float, float, float]] = None
    quote: constr(min_length=1)


class ExtractedTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: List[List[str]]
    coords: List[Tuple[float, float, float, float]]


class ExtractedPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int
    text_blocks: List[str]
    tables: List[ExtractedTable]


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str
    pages: List[ExtractedPage]


class AtomicRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    text: str
    source: Tuple[SourceRef, ...]
    needs_manual_review: bool = False


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    status: CheckStatus
    impact: Impact
    severity: Severity
    finding_type: FindingType
    evidence: List[Dict]
    rationale: Optional[str] = None
    question: Optional[str] = None


class ScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    description: str
    evidence: List[Dict]


class RequirementAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: AtomicRequirement
    checks: Tuple[CheckResult, ...]
    scenarios: Tuple[ScenarioResult, ...]


class RequirementScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    score: int


class RequirementReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis: RequirementAnalysis
    score: RequirementScore


class AggregateScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int
    breakdown: Dict[str, int]


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    run_id: Optional[str]
    reviews: Tuple[RequirementReview, ...]
    aggregate: AggregateScore
    failures: List[Dict]


class ReportArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    json_path: str
    markdown_path: str
    docx_path: Optional[str] = None
    status: str


class PreparedReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    total_requirements: int
    warnings: List[str]
    artifacts: ReportArtifacts


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    stage: str
    completed: bool


class ReviewError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    details: Dict[str, object]


class AnalysisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    requirements: Tuple[RequirementAnalysis, ...]


def generate_schema() -> str:
    return AnalysisSubmission.model_json_schema(sort_keys=True)
