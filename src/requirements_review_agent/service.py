from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .analysis import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisBatch,
    build_analysis_batch,
    validate_submission,
)
from .errors import (
    ANALYSIS_INVALID,
    PROVIDER_UNAVAILABLE,
    RULE_PACK_INVALID,
    ReviewError,
    ReviewException,
)
from .models import (
    AnalysisSubmission,
    ApplicableRule,
    AtomicRequirement,
    PreparedReview,
    ProviderMode,
    ReportArtifacts,
    RequirementAnalysis,
    RequirementReview,
    ReviewReport,
    RunStatus,
)
from .normalizer import normalize_requirements_with_diagnostics
from .pdf_extractor import extract_pdf
from .providers import AnalysisProvider, build_provider
from .reporting import render_all
from .rules import load_bundled_rule_pack, load_rule_pack, select_applicable_rules
from .scoring import aggregate_scores, score_requirements
from .storage import RunStore

SCHEMA_VERSION = ANALYSIS_SCHEMA_VERSION
DEFAULT_ANALYSIS_BATCH_SIZE = 50
_SERVICE_STAGES = {"prepared", "analyzed", "finalized", "partial", "failed"}


def _analysis_invalid(message: str, **details: object) -> ReviewException:
    return ReviewException(
        ReviewError(
            code=ANALYSIS_INVALID,
            message=message,
            details={key: value for key, value in details.items() if value is not None},
        )
    )


def _rule_pack_invalid(message: str, **details: object) -> ReviewException:
    return ReviewException(
        ReviewError(
            code=RULE_PACK_INVALID,
            message=message,
            details={key: value for key, value in details.items() if value is not None},
        )
    )


def _provider_unavailable(message: str, **details: object) -> ReviewException:
    return ReviewException(
        ReviewError(
            code=PROVIDER_UNAVAILABLE,
            message=message,
            details={key: value for key, value in details.items() if value is not None},
        )
    )


class ReviewService:
    def __init__(
        self,
        workspace: Path,
        *,
        rules_root: Path | None = None,
        provider_factory: Callable[[ProviderMode], AnalysisProvider | None] | None = None,
        analysis_batch_size: int = DEFAULT_ANALYSIS_BATCH_SIZE,
    ) -> None:
        if analysis_batch_size < 1:
            raise ValueError("analysis_batch_size must be positive")
        self.workspace = workspace
        self._workspace = workspace.resolve()
        self._rules_root = (rules_root or (workspace / "rules")).resolve()
        self._store = RunStore(self._workspace)
        self._provider_factory = provider_factory or self._default_provider_factory
        self._analysis_batch_size = analysis_batch_size

    def prepare(self, pdf: Path, rule_pack: str, mode: ProviderMode) -> PreparedReview:
        rule_path = self._resolve_rule_pack(rule_pack)
        extracted = extract_pdf(pdf, self._workspace)
        normalization = normalize_requirements_with_diagnostics(extracted)
        requirements = normalization.requirements
        if not requirements:
            raise _analysis_invalid("未提取到可分析的需求", stage="prepare")

        pack = (
            load_rule_pack(rule_path)
            if rule_path is not None
            else load_bundled_rule_pack(rule_pack)
        )
        applicable = {
            requirement.requirement_id: select_applicable_rules(requirement, pack)
            for requirement in requirements
        }
        warning_items = [
            requirement.requirement_id
            for requirement in requirements
            if requirement.needs_manual_review
        ]
        filtered_count = sum(normalization.filtered_counts.values())
        if filtered_count:
            warning_items.append(
                "normalization:"
                f"candidates={len(requirements) + filtered_count};"
                f"kept={len(requirements)};filtered={filtered_count}"
            )
            warning_items.extend(
                f"normalization:filtered:{reason}={count}"
                for reason, count in normalization.filtered_counts.items()
            )
        warnings = tuple(warning_items)
        data_destination = self._data_destination(mode)
        model_name = self._model_name_for_mode(mode)
        batch_count = (
            len(requirements) + self._analysis_batch_size - 1
        ) // self._analysis_batch_size
        manifest = self._store.create_run(
            pdf_hash=extracted.sha256,
            rule_version=pack.version,
            model_mode=mode,
            schema_version=SCHEMA_VERSION,
        )
        state: dict[str, object] = {
            "run_id": manifest.run_id,
            "stage": "prepared",
            "provider_mode": mode.value,
            "model_name": model_name,
            "data_destination": data_destination,
            "warnings": list(warnings),
            "requirement_count": len(requirements),
            "analyzed_count": 0,
            "batch_count": batch_count,
            "analysis_batch_size": self._analysis_batch_size,
            "submitted_batch_indices": [],
            "artifacts": None,
            "rule_version": pack.version,
        }
        self._store.write_stage(manifest.run_id, "extracted", extracted.model_dump(mode="json"))
        self._store.write_stage(
            manifest.run_id,
            "requirements",
            {"requirements": [item.model_dump(mode="json") for item in requirements]},
        )
        self._store.write_stage(
            manifest.run_id,
            "applicable",
            {
                "applicable": {
                    requirement_id: [rule.model_dump(mode="json") for rule in rules]
                    for requirement_id, rules in applicable.items()
                }
            },
        )
        self._store.write_stage(manifest.run_id, "service", state)
        return PreparedReview(
            run_id=manifest.run_id,
            provider_mode=mode,
            data_destination=data_destination,
            requirement_count=len(requirements),
            warnings=warnings,
            batch_count=batch_count,
        )

    def get_batch(self, run_id: str, batch_index: int) -> AnalysisBatch:
        state = self._load_state(run_id)
        if state["stage"] not in {"prepared", "analyzed"}:
            raise _analysis_invalid(
                "当前阶段不允许读取分析批次",
                run_id=run_id,
                stage=state["stage"],
            )
        batch_count = self._state_batch_count(state)
        if batch_index < 0 or batch_index >= batch_count:
            raise _analysis_invalid(
                "批次索引越界",
                run_id=run_id,
                stage=state["stage"],
                index=batch_index,
            )
        requirements = self._load_requirements(run_id)
        applicable = self._load_applicable(run_id)
        batch_requirements = self._requirements_for_batch(state, requirements, batch_index)
        batch_applicable = {
            requirement.requirement_id: applicable[requirement.requirement_id]
            for requirement in batch_requirements
        }
        return build_analysis_batch(run_id, batch_index, batch_requirements, batch_applicable)

    def submit(
        self, run_id: str, submission: AnalysisSubmission | dict[str, object]
    ) -> RunStatus:
        state = self._load_state(run_id)
        mode = ProviderMode(state["provider_mode"])
        if mode is not ProviderMode.COPILOT:
            raise _analysis_invalid(
                "当前模型模式不允许手动提交分析结果",
                run_id=run_id,
                stage=state["stage"],
            )
        if state["stage"] != "prepared":
            raise _analysis_invalid(
                "当前阶段不允许提交分析结果",
                run_id=run_id,
                stage=state["stage"],
            )

        parsed = self._parse_submission(submission)
        requirements = self._load_requirements(run_id)
        applicable = self._load_applicable(run_id)
        batch_index = self._submission_batch_index(state, requirements, parsed)
        submitted_indices = self._submitted_batch_indices(state)
        if batch_index in submitted_indices:
            raise _analysis_invalid(
                "分析批次已提交",
                run_id=run_id,
                stage=state["stage"],
                index=batch_index,
            )

        batch_requirements = self._requirements_for_batch(state, requirements, batch_index)
        batch_applicable = {
            requirement.requirement_id: applicable[requirement.requirement_id]
            for requirement in batch_requirements
        }
        analyses = validate_submission(parsed, batch_requirements, batch_applicable)
        self._store.write_stage(
            run_id,
            self._batch_submission_stage(batch_index),
            parsed.model_dump(mode="json"),
        )
        submitted_indices.add(batch_index)
        state["submitted_batch_indices"] = sorted(submitted_indices)
        state["analyzed_count"] = int(state.get("analyzed_count", 0)) + len(analyses)
        if len(submitted_indices) == self._state_batch_count(state):
            merged = self._merge_batch_submissions(run_id, state)
            validate_submission(merged, requirements, applicable)
            self._store.write_stage(run_id, "submission", merged.model_dump(mode="json"))
            state["stage"] = "analyzed"
        self._store.write_stage(run_id, "service", state)
        return self._status_from_state(run_id, state)

    async def run_provider(self, run_id: str) -> RunStatus:
        state = self._load_state(run_id)
        mode = ProviderMode(state["provider_mode"])
        if mode is ProviderMode.COPILOT:
            raise _analysis_invalid(
                "Copilot 模式必须通过 submit 提交分析结果",
                run_id=run_id,
                stage=state["stage"],
            )
        if state["stage"] != "prepared":
            raise _analysis_invalid(
                "当前阶段不允许运行 provider 分析",
                run_id=run_id,
                stage=state["stage"],
            )

        provider = self._provider_factory(mode)
        if provider is None:
            raise _provider_unavailable("Provider 不可用", run_id=run_id, stage=state["stage"])

        try:
            requirements = self._load_requirements(run_id)
            applicable = self._load_applicable(run_id)
            submitted_indices = self._submitted_batch_indices(state)
            for batch_index in range(self._state_batch_count(state)):
                if batch_index in submitted_indices:
                    continue
                batch = self.get_batch(run_id, batch_index)
                submission = await provider.analyze(batch)
                analyses = validate_submission(
                    submission, batch.requirements, batch.applicable
                )
                self._store.write_stage(
                    run_id,
                    self._batch_submission_stage(batch_index),
                    submission.model_dump(mode="json"),
                )
                submitted_indices.add(batch_index)
                state["submitted_batch_indices"] = sorted(submitted_indices)
                state["analyzed_count"] = int(state.get("analyzed_count", 0)) + len(
                    analyses
                )
                self._store.write_stage(run_id, "service", state)

            merged = self._merge_batch_submissions(run_id, state)
            validate_submission(merged, requirements, applicable)
            self._store.write_stage(run_id, "submission", merged.model_dump(mode="json"))
            state["stage"] = "analyzed"
            state["analyzed_count"] = len(requirements)
            self._store.write_stage(run_id, "service", state)
            return self._status_from_state(run_id, state)
        finally:
            await provider.aclose()

    def finalize(self, run_id: str) -> ReportArtifacts:
        state = self._load_state(run_id)
        if state["stage"] != "analyzed":
            raise _analysis_invalid("当前阶段不允许生成报告", run_id=run_id, stage=state["stage"])

        requirements = self._load_requirements(run_id)
        applicable = self._load_applicable(run_id)
        submission = self._load_submission(run_id)
        analyses = validate_submission(submission, requirements, applicable)
        scores = score_requirements(analyses, applicable)
        aggregate = aggregate_scores(scores)
        score_by_id = {score.requirement_id: score for score in scores}
        analysis_by_id = {analysis.requirement_id: analysis for analysis in analyses}
        reviews = tuple(
            RequirementReview(
                requirement=requirement,
                analysis=analysis_by_id[requirement.requirement_id],
                score=score_by_id[requirement.requirement_id],
            )
            for requirement in requirements
        )
        report = ReviewReport(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            provider_mode=ProviderMode(state["provider_mode"]),
            model_name=self._maybe_string(state.get("model_name")),
            rule_version=self._maybe_string(state.get("rule_version")) or "",
            requirements=reviews,
            aggregate=aggregate,
            failures=self._load_failures(run_id),
        )
        reports_dir = self._store.run_path(run_id) / "reports"
        artifacts = render_all(report, reports_dir)
        report_payload = json.loads(artifacts.json.read_text(encoding="utf-8"))
        self._store.write_stage(run_id, "report", report_payload)
        if artifacts.status == "partial":
            state["stage"] = "partial"
        else:
            state["stage"] = "finalized"
        state["artifacts"] = artifacts.model_dump(mode="json", by_alias=True)
        self._store.write_stage(run_id, "service", state)
        return artifacts

    def status(self, run_id: str) -> RunStatus:
        state = self._load_state(run_id)
        return self._status_from_state(run_id, state)

    def _default_provider_factory(self, mode: ProviderMode) -> AnalysisProvider | None:
        return build_provider(mode, os.environ)

    def _resolve_rule_pack(self, rule_pack: str) -> Path | None:
        candidate = Path(rule_pack)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise _rule_pack_invalid("规则包路径非法", rule_pack=rule_pack)
        if candidate.suffix not in {"", ".yaml"}:
            raise _rule_pack_invalid("规则包路径非法", rule_pack=rule_pack)
        filename = candidate.name if candidate.suffix == ".yaml" else f"{candidate.name}.yaml"
        if filename != candidate.name and len(candidate.parts) != 1:
            raise _rule_pack_invalid("规则包路径非法", rule_pack=rule_pack)
        resolved = (self._rules_root / filename).resolve()
        if not resolved.is_relative_to(self._rules_root):
            raise _rule_pack_invalid("规则包路径非法", rule_pack=rule_pack)
        return resolved if resolved.exists() else None

    def _load_state(self, run_id: str) -> dict[str, Any]:
        try:
            state = self._store.read_stage(run_id, "service")
            stage = state.get("stage")
            provider_mode = state.get("provider_mode")
            if stage not in _SERVICE_STAGES or not isinstance(provider_mode, str):
                raise ValueError("invalid service state")
            ProviderMode(provider_mode)
            return state
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
            raise _analysis_invalid("运行状态不存在或已损坏", run_id=run_id) from exc

    def _load_requirements(self, run_id: str) -> tuple[AtomicRequirement, ...]:
        try:
            payload = self._store.read_stage(run_id, "requirements")
            items = payload.get("requirements", [])
            if not isinstance(items, list):
                raise ValueError("invalid requirements")
            return tuple(AtomicRequirement.model_validate(item) for item in items)
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise _analysis_invalid("requirements 阶段数据无效", run_id=run_id) from exc

    def _load_applicable(self, run_id: str) -> dict[str, tuple[ApplicableRule, ...]]:
        try:
            payload = self._store.read_stage(run_id, "applicable")
            mapping = payload.get("applicable", {})
            if not isinstance(mapping, dict):
                raise ValueError("invalid applicable rules")
            applicable: dict[str, tuple[ApplicableRule, ...]] = {}
            for requirement_id, rules in mapping.items():
                if not isinstance(requirement_id, str) or not isinstance(rules, list):
                    raise ValueError("invalid applicable rules")
                applicable[requirement_id] = tuple(
                    ApplicableRule.model_validate(rule) for rule in rules
                )
            return applicable
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise _analysis_invalid("applicable 阶段数据无效", run_id=run_id) from exc

    def _load_submission(self, run_id: str) -> AnalysisSubmission:
        try:
            payload = self._store.read_stage(run_id, "submission")
            return AnalysisSubmission.model_validate(payload)
        except (FileNotFoundError, OSError, ValueError, TypeError, ValidationError) as exc:
            raise _analysis_invalid(
                "分析结果不存在或已损坏",
                run_id=run_id,
            ) from exc

    def _parse_submission(
        self, submission: AnalysisSubmission | dict[str, object]
    ) -> AnalysisSubmission:
        if isinstance(submission, AnalysisSubmission):
            return submission
        try:
            return AnalysisSubmission.model_validate(submission)
        except ValidationError as exc:
            first_error = exc.errors(include_url=False)[0]
            raise _analysis_invalid(
                "分析结果结构无效",
                location=".".join(str(part) for part in first_error.get("loc", ())),
                error=str(first_error.get("msg", "validation error"))[:200],
            ) from exc

    def _state_batch_count(self, state: dict[str, Any]) -> int:
        batch_count = state.get("batch_count", 1)
        if not isinstance(batch_count, int) or isinstance(batch_count, bool) or batch_count < 1:
            raise _analysis_invalid("运行批次状态无效")
        return batch_count

    def _state_batch_size(self, state: dict[str, Any]) -> int:
        batch_size = state.get("analysis_batch_size", state.get("requirement_count", 0))
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise _analysis_invalid("运行批次状态无效")
        return batch_size

    def _requirements_for_batch(
        self,
        state: dict[str, Any],
        requirements: tuple[AtomicRequirement, ...],
        batch_index: int,
    ) -> tuple[AtomicRequirement, ...]:
        batch_size = self._state_batch_size(state)
        start = batch_index * batch_size
        return requirements[start : start + batch_size]

    def _submitted_batch_indices(self, state: dict[str, Any]) -> set[int]:
        raw_indices = state.get("submitted_batch_indices", [])
        if not isinstance(raw_indices, list) or any(
            not isinstance(index, int) or isinstance(index, bool) for index in raw_indices
        ):
            raise _analysis_invalid("运行批次状态无效")
        indices = set(raw_indices)
        if len(indices) != len(raw_indices) or any(
            index < 0 or index >= self._state_batch_count(state) for index in indices
        ):
            raise _analysis_invalid("运行批次状态无效")
        return indices

    def _submission_batch_index(
        self,
        state: dict[str, Any],
        requirements: tuple[AtomicRequirement, ...],
        submission: AnalysisSubmission,
    ) -> int:
        submitted_ids = {analysis.requirement_id for analysis in submission.requirements}
        for batch_index in range(self._state_batch_count(state)):
            batch_requirements = self._requirements_for_batch(state, requirements, batch_index)
            expected_ids = {
                requirement.requirement_id for requirement in batch_requirements
            }
            if submitted_ids == expected_ids:
                return batch_index
        raise _analysis_invalid("提交内容不匹配任何分析批次")

    def _batch_submission_stage(self, batch_index: int) -> str:
        return f"submission_{batch_index:06d}"

    def _merge_batch_submissions(
        self, run_id: str, state: dict[str, Any]
    ) -> AnalysisSubmission:
        analyses: list[RequirementAnalysis] = []
        for batch_index in range(self._state_batch_count(state)):
            try:
                payload = self._store.read_stage(
                    run_id, self._batch_submission_stage(batch_index)
                )
                batch_submission = AnalysisSubmission.model_validate(payload)
            except (FileNotFoundError, OSError, ValueError, TypeError, ValidationError) as exc:
                raise _analysis_invalid(
                    "分析批次结果不存在或已损坏",
                    run_id=run_id,
                    index=batch_index,
                ) from exc
            analyses.extend(batch_submission.requirements)
        return AnalysisSubmission(schema_version=SCHEMA_VERSION, requirements=tuple(analyses))

    def _load_failures(self, run_id: str) -> tuple[ReviewError, ...]:
        failures_path = self._store.run_path(run_id) / "failures.json"
        if not failures_path.exists():
            return ()
        try:
            payload = json.loads(failures_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise _analysis_invalid("failures 数据无效", run_id=run_id) from exc
        if not isinstance(payload, list):
            raise _analysis_invalid("failures 数据无效", run_id=run_id)
        failures: list[ReviewError] = []
        for item in payload:
            if not isinstance(item, dict):
                raise _analysis_invalid("failures 数据无效", run_id=run_id)
            code = item.get("code")
            message = item.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                raise _analysis_invalid("failures 数据无效", run_id=run_id)
            failures.append(
                ReviewError(
                    code=code,
                    message=message,
                    details={
                        key: value
                        for key, value in item.items()
                        if key not in {"code", "message"}
                    },
                )
            )
        return tuple(failures)

    def _status_from_state(self, run_id: str, state: dict[str, Any]) -> RunStatus:
        artifacts_payload = state.get("artifacts")
        artifacts = None
        if isinstance(artifacts_payload, dict):
            artifacts = ReportArtifacts.model_validate(artifacts_payload)
        warnings = state.get("warnings", [])
        return RunStatus(
            run_id=run_id,
            stage=self._maybe_string(state.get("stage")) or "unknown",
            requirement_count=int(state.get("requirement_count", 0)),
            analyzed_count=int(state.get("analyzed_count", 0)),
            warnings=tuple(str(item) for item in warnings if isinstance(item, str)),
            artifacts=artifacts,
        )

    def _data_destination(self, mode: ProviderMode) -> str:
        if mode is ProviderMode.COPILOT:
            return "GitHub Copilot model selected in VS Code"
        if mode is ProviderMode.COMPANY_API:
            return "Company-approved API selected via local environment configuration"
        return "Local model endpoint selected in this workspace"

    def _model_name_for_mode(self, mode: ProviderMode) -> str | None:
        if mode is ProviderMode.COMPANY_API:
            return os.environ.get("RRA_COMPANY_MODEL") or None
        if mode is ProviderMode.LOCAL:
            return os.environ.get("RRA_LOCAL_MODEL") or None
        return None

    def _maybe_string(self, value: object) -> str | None:
        return value if isinstance(value, str) and value else None
