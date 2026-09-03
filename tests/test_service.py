from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from requirements_review_agent.analysis import AnalysisBatch
from requirements_review_agent.errors import (
    ANALYSIS_INVALID,
    RULE_PACK_INVALID,
    ReviewError,
    ReviewException,
)
from requirements_review_agent.models import (
    AnalysisSubmission,
    CheckResult,
    CheckStatus,
    FindingType,
    ProviderMode,
    RequirementAnalysis,
    Severity,
    SourceRef,
)
from requirements_review_agent.providers.base import AnalysisProvider
from requirements_review_agent.service import ReviewService
from requirements_review_agent.storage import RunStore


def build_pdf(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 500, 220),
        "Connectivity:\n"
        "1. Device enters Wi-Fi pairing state within 30 seconds.\n"
        "2. BLE reconnect timeout is 5 s and should recover automatically.",
        fontsize=12,
    )
    document.save(path)
    document.close()
    return path

def build_pdf_with_metadata(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 60), "Document ID: PRS-1234", fontsize=10)
    page.insert_text((50, 120), "Device shall stop within 3 seconds.", fontsize=12)
    document.save(path)
    document.close()
    return path


def test_prepare_uses_bundled_rule_pack_when_workspace_has_no_rules(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "requirements.pdf")

    prepared = ReviewService(tmp_path).prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)

    assert prepared.requirement_count == 1

def test_prepare_reports_normalization_filter_counts(tmp_path: Path) -> None:
    pdf = build_pdf_with_metadata(tmp_path / "requirements.pdf")

    prepared = ReviewService(tmp_path).prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)

    assert prepared.requirement_count == 1
    assert "normalization:candidates=2;kept=1;filtered=1" in prepared.warnings
    assert "normalization:filtered:document_metadata=1" in prepared.warnings


def test_prepare_prefers_workspace_rule_pack_over_bundled(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "home-iot-v1.yaml").write_text(
        """
version: "1.0"
rules:
  - id: workspace.override
    question: "请确认 workspace override 已应用。"
    weight: 1
    impact: both
    scenario_category: override
    always: true
    keywords: []
""".strip(),
        encoding="utf-8",
    )
    pdf = build_pdf(tmp_path / "requirements.pdf")
    service = ReviewService(tmp_path)

    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)

    assert {
        rule.rule_id
        for applicable in batch.applicable.values()
        for rule in applicable
    } == {"workspace.override"}


def requirement_source(requirement: object) -> SourceRef:
    return SourceRef.model_validate(requirement.sources[0].model_dump(mode="json"))


def valid_submission_for(batch: AnalysisBatch) -> AnalysisSubmission:
    analyses: list[RequirementAnalysis] = []
    for requirement in batch.requirements:
        applicable_rules = batch.applicable[requirement.requirement_id]
        scenario_categories: list[str] = []
        checks: list[CheckResult] = []
        evidence = (requirement_source(requirement),)
        for rule in applicable_rules:
            if rule.scenario_category and rule.scenario_category not in scenario_categories:
                scenario_categories.append(rule.scenario_category)
            checks.append(
                CheckResult(
                    rule_id=rule.rule_id,
                    status=CheckStatus.COMPLETE,
                    impact=rule.impact,
                    severity=Severity.NORMAL,
                    finding_type=FindingType.FACT,
                    evidence=evidence,
                    rationale="原文已明确说明。",
                    question=None,
                    confidence=0.95,
                )
            )
        analyses.append(
            RequirementAnalysis.model_validate(
                {
                    "requirement_id": requirement.requirement_id,
                    "checks": [check.model_dump(mode="json") for check in checks],
                    "scenarios": [
                        {
                            "category": category,
                            "description": f"验证 {category} 场景。",
                            "covered": True,
                            "evidence": [item.model_dump(mode="json") for item in evidence],
                        }
                        for category in scenario_categories
                    ],
                }
            )
        )

    return AnalysisSubmission(schema_version="1.0", requirements=tuple(analyses))


class FakeProvider(AnalysisProvider):
    def __init__(self, submission: AnalysisSubmission) -> None:
        self.submission = submission
        self.called_batches: list[AnalysisBatch] = []
        self.closed = False

    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission:
        self.called_batches.append(batch)
        return self.submission

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "home-iot-v1.yaml").write_text(
        (Path("rules") / "home-iot-v1.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def pdf(workspace: Path) -> Path:
    return build_pdf(workspace / "input.pdf")


@pytest.fixture
def service(workspace: Path) -> ReviewService:
    return ReviewService(workspace)


def test_copilot_prepare_batch_submit_finalize_status_roundtrip(
    service: ReviewService, pdf: Path
) -> None:
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)

    assert prepared.data_destination == "GitHub Copilot model selected in VS Code"
    assert prepared.batch_count == 1
    assert prepared.requirement_count >= 1

    batch = service.get_batch(prepared.run_id, 0)
    assert batch.run_id == prepared.run_id
    assert batch.batch_index == 0
    assert batch.analysis_submission_schema["title"] == "AnalysisSubmission"
    assert "请逐条分析" in batch.instructions

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        service.finalize(prepared.run_id)

    analyzed = service.submit(prepared.run_id, valid_submission_for(batch))
    assert analyzed.stage == "analyzed"
    assert analyzed.analyzed_count == prepared.requirement_count

    artifacts = service.finalize(prepared.run_id)
    assert artifacts.status == "complete"
    assert artifacts.json.exists()
    assert artifacts.markdown.exists()
    assert artifacts.docx is not None
    assert artifacts.docx.exists()

    status = service.status(prepared.run_id)
    assert status.stage == "finalized"
    assert status.requirement_count == prepared.requirement_count
    assert status.analyzed_count == prepared.requirement_count
    assert status.artifacts is not None
    assert status.artifacts.json.exists()


def test_new_service_instance_can_resume_status_batch_and_finalize(
    workspace: Path, pdf: Path
) -> None:
    service = ReviewService(workspace)
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)
    service.submit(prepared.run_id, valid_submission_for(batch))

    resumed = ReviewService(workspace)
    status = resumed.status(prepared.run_id)
    assert status.stage == "analyzed"
    resumed_batch = resumed.get_batch(prepared.run_id, 0)
    assert resumed_batch.run_id == prepared.run_id

    artifacts = resumed.finalize(prepared.run_id)
    assert artifacts.json.exists()
    assert resumed.status(prepared.run_id).stage == "finalized"


def test_finalize_restores_recorded_failure_summaries(workspace: Path, pdf: Path) -> None:
    service = ReviewService(workspace)
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)
    service.submit(prepared.run_id, valid_submission_for(batch))
    RunStore(workspace).record_failure(
        prepared.run_id,
        ReviewError(
            code=ANALYSIS_INVALID,
            message="一条需求需要人工确认。",
            details={"requirement_id": batch.requirements[0].requirement_id},
        ),
    )

    artifacts = ReviewService(workspace).finalize(prepared.run_id)
    report = json.loads(artifacts.json.read_text(encoding="utf-8"))

    assert report["failures"] == [
        {
            "code": ANALYSIS_INVALID,
            "message": "一条需求需要人工确认。",
            "details": {"requirement_id": batch.requirements[0].requirement_id},
        }
    ]


def test_corrupt_submission_returns_stable_review_error(workspace: Path, pdf: Path) -> None:
    service = ReviewService(workspace)
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)
    service.submit(prepared.run_id, valid_submission_for(batch))
    RunStore(workspace).write_stage(prepared.run_id, "submission", {"requirements": "secret"})

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID) as exc_info:
        ReviewService(workspace).finalize(prepared.run_id)

    assert "secret" not in str(exc_info.value)


def test_finalize_keeps_analyzed_stage_when_report_stage_write_fails(
    workspace: Path,
    pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ReviewService(workspace)
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)
    service.submit(prepared.run_id, valid_submission_for(batch))
    original_write_stage = service._store.write_stage

    def fail_report_stage(run_id: str, stage_name: str, payload: dict[str, object]) -> None:
        if stage_name == "report":
            raise OSError("disk full")
        original_write_stage(run_id, stage_name, payload)

    monkeypatch.setattr(service._store, "write_stage", fail_report_stage)

    with pytest.raises(OSError, match="disk full"):
        service.finalize(prepared.run_id)

    assert ReviewService(workspace).status(prepared.run_id).stage == "analyzed"


def test_submit_validates_before_storage_and_keeps_prepared_stage_on_failure(
    service: ReviewService, pdf: Path
) -> None:
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    batch = service.get_batch(prepared.run_id, 0)
    invalid_payload = valid_submission_for(batch).model_dump(mode="json")
    invalid_payload["requirements"] = []

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        service.submit(prepared.run_id, invalid_payload)

    status = service.status(prepared.run_id)
    assert status.stage == "prepared"
    assert status.analyzed_count == 0


@pytest.mark.asyncio
async def test_local_provider_run_persists_analysis_and_closes_provider(
    workspace: Path, pdf: Path
) -> None:
    seed_service = ReviewService(workspace)
    prepared = seed_service.prepare(pdf, "home-iot-v1", ProviderMode.LOCAL)
    batch = seed_service.get_batch(prepared.run_id, 0)
    fake_provider = FakeProvider(valid_submission_for(batch))
    service = ReviewService(
        workspace,
        provider_factory=lambda mode: fake_provider if mode is ProviderMode.LOCAL else None,
    )

    status = await service.run_provider(prepared.run_id)

    assert status.stage == "analyzed"
    assert status.analyzed_count == prepared.requirement_count
    assert fake_provider.called_batches
    assert fake_provider.closed is True


@pytest.mark.asyncio
async def test_provider_submission_with_stale_schema_is_rejected_and_closed(
    workspace: Path, pdf: Path
) -> None:
    seed_service = ReviewService(workspace)
    prepared = seed_service.prepare(pdf, "home-iot-v1", ProviderMode.LOCAL)
    batch = seed_service.get_batch(prepared.run_id, 0)
    valid = valid_submission_for(batch)
    stale = AnalysisSubmission.model_construct(
        schema_version="999.0",
        requirements=valid.requirements,
    )
    fake_provider = FakeProvider(stale)
    service = ReviewService(workspace, provider_factory=lambda _: fake_provider)

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        await service.run_provider(prepared.run_id)

    assert fake_provider.closed is True
    assert service.status(prepared.run_id).stage == "prepared"


@pytest.mark.asyncio
async def test_run_provider_rejects_copilot_and_submit_rejects_local_mode(
    service: ReviewService, workspace: Path, pdf: Path
) -> None:
    copilot_prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        await service.run_provider(copilot_prepared.run_id)

    local_service = ReviewService(workspace)
    local_prepared = local_service.prepare(pdf, "home-iot-v1", ProviderMode.LOCAL)
    local_batch = local_service.get_batch(local_prepared.run_id, 0)
    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        local_service.submit(local_prepared.run_id, valid_submission_for(local_batch))


def test_status_never_contains_requirement_text(service: ReviewService, pdf: Path) -> None:
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)

    status = service.status(prepared.run_id)
    dumped = status.model_dump(mode="json")
    encoded = json.dumps(dumped, ensure_ascii=False, sort_keys=True)

    assert "Device enters Wi-Fi pairing state within 30 seconds." not in encoded
    assert "BLE reconnect timeout is 5 s" not in encoded


def test_illegal_batch_index_and_stage_transitions_raise_stable_errors(
    service: ReviewService, pdf: Path
) -> None:
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        service.get_batch(prepared.run_id, 1)

    batch = service.get_batch(prepared.run_id, 0)
    service.submit(prepared.run_id, valid_submission_for(batch))

    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        service.submit(prepared.run_id, valid_submission_for(batch))

    service.finalize(prepared.run_id)
    with pytest.raises(ReviewException, match=ANALYSIS_INVALID):
        service.get_batch(prepared.run_id, 0)


def test_rule_pack_traversal_is_rejected(service: ReviewService, pdf: Path) -> None:
    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        service.prepare(pdf, "../rules/home-iot-v1", ProviderMode.COPILOT)

    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        service.prepare(pdf, "C:/temp/home-iot-v1.yaml", ProviderMode.COPILOT)


def test_rule_pack_with_non_yaml_suffix_is_rejected(
    service: ReviewService, workspace: Path, pdf: Path
) -> None:
    (workspace / "rules" / "home-iot-v1.txt.yaml").write_text(
        (workspace / "rules" / "home-iot-v1.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ReviewException, match=RULE_PACK_INVALID):
        service.prepare(pdf, "home-iot-v1.txt", ProviderMode.COPILOT)