from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .errors import ReviewError
from .models import ProviderMode, RunManifest

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_STAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HASH_CHUNK_SIZE = 1024 * 1024


def _coerce_model_mode(model_mode: ProviderMode | str) -> ProviderMode:
    return model_mode if isinstance(model_mode, ProviderMode) else ProviderMode(model_mode)


def _ensure_safe_name(value: str, *, field_name: str, pattern: re.Pattern[str]) -> str:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if value in {".", ".."}:
        raise ValueError(f"{field_name} must not be a traversal segment")

    candidate = Path(value)
    if candidate.is_absolute() or any(separator in value for separator in ("/", "\\")):
        raise ValueError(f"{field_name} must be a local name")
    if ".." in candidate.parts:
        raise ValueError(f"{field_name} must not traverse directories")
    if not pattern.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe characters")
    return value


def _hash_file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(
    path: Path,
    value: BaseModel | dict[str, object] | list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload: object = value.model_dump(mode="json") if isinstance(value, BaseModel) else value

    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


class RunStore:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._runs_root = (self._workspace / ".runs").resolve()
        self._runs_root.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        *,
        pdf_hash: str,
        rule_version: str,
        model_mode: ProviderMode | str,
        schema_version: str,
    ) -> RunManifest:
        normalized_mode = _coerce_model_mode(model_mode)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{timestamp}-{secrets.token_hex(4)}"
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest = RunManifest(
            run_id=run_id,
            pdf_hash=pdf_hash,
            rule_version=rule_version,
            model_mode=normalized_mode,
            schema_version=schema_version,
            stage="created",
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        _write_json_atomic(run_dir / "manifest.json", manifest)
        return manifest

    def write_stage(self, run_id: str, stage_name: str, payload: dict[str, object]) -> None:
        stage_path = self._stage_path(run_id, stage_name)
        _write_json_atomic(stage_path, payload)

    def read_stage(self, run_id: str, stage_name: str) -> dict[str, Any]:
        stage_path = self._stage_path(run_id, stage_name)
        with stage_path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("stage payload must be a JSON object")
        return data

    def can_resume(
        self,
        run_id: str,
        pdf_hash: str,
        rule_version: str,
        model_mode: ProviderMode | str,
        schema_version: str,
    ) -> bool:
        manifest = self._read_manifest(run_id)
        return (
            manifest.pdf_hash == pdf_hash
            and manifest.rule_version == rule_version
            and manifest.model_mode is _coerce_model_mode(model_mode)
            and manifest.schema_version == schema_version
        )

    def record_failure(self, run_id: str, error: ReviewError) -> None:
        failures_path = self._run_dir(run_id) / "failures.json"
        failures: list[dict[str, object]] = []
        if failures_path.exists():
            with failures_path.open("r", encoding="utf-8") as stream:
                existing = json.load(stream)
            if not isinstance(existing, list):
                raise ValueError("failures payload must be a JSON array")
            failures = [entry for entry in existing if isinstance(entry, dict)]

        details = error.details
        summary: dict[str, object] = {
            "code": error.code,
            "message": error.message,
        }
        for key in ("requirement_id", "rule_id", "batch_id", "run_id"):
            value = details.get(key)
            if isinstance(value, str) and value:
                summary[key] = value

        error_summary = details.get("error") or details.get("summary")
        if isinstance(error_summary, str) and error_summary:
            summary["error"] = error_summary

        failures.append(summary)
        _write_json_atomic(failures_path, failures)

    def _run_dir(self, run_id: str) -> Path:
        safe_run_id = _ensure_safe_name(run_id, field_name="run_id", pattern=_RUN_ID_RE)
        run_dir = (self._runs_root / safe_run_id).resolve()
        if not run_dir.is_relative_to(self._runs_root):
            raise ValueError("run path escapes runs root")
        return run_dir

    def _stage_path(self, run_id: str, stage_name: str) -> Path:
        safe_stage_name = _ensure_safe_name(stage_name, field_name="stage_name", pattern=_STAGE_RE)
        stage_path = (self._run_dir(run_id) / f"{safe_stage_name}.json").resolve()
        if not stage_path.is_relative_to(self._runs_root):
            raise ValueError("stage path escapes runs root")
        return stage_path

    def _read_manifest(self, run_id: str) -> RunManifest:
        manifest_path = self._run_dir(run_id) / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return RunManifest.model_validate(payload)
