from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from requirements_review_agent.errors import ReviewError
from requirements_review_agent.models import ProviderMode
from requirements_review_agent.storage import RunStore, _hash_file_sha256


def test_create_run_persists_manifest_and_stage_roundtrip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)

    manifest = store.create_run(
        pdf_hash="abc123",
        rule_version="2026-09-01",
        model_mode=ProviderMode.COPILOT,
        schema_version="1.0",
    )

    assert manifest.run_id
    assert re.fullmatch(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$", manifest.run_id)
    assert manifest.model_mode is ProviderMode.COPILOT
    assert manifest.stage == "created"

    manifest_path = tmp_path / ".runs" / manifest.run_id / "manifest.json"
    assert manifest_path.exists()
    stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stored_manifest["run_id"] == manifest.run_id
    assert stored_manifest["model_mode"] == "copilot"

    payload = {"requirements": [{"requirement_id": "REQ-1", "text": "系统应记录告警"}]}
    store.write_stage(manifest.run_id, "extracted", payload)

    assert store.read_stage(manifest.run_id, "extracted") == payload


@pytest.mark.parametrize(
    "run_id",
    [
        "run-1",
        "20260902T103233Z-ABCDEF12",
        "20260902T103233-abcdef12",
        "20260902T103233Z-abcdef123",
        "20260902t103233Z-abcdef12",
    ],
)
def test_rejects_run_id_not_matching_timestamp_and_hex_format(
    tmp_path: Path, run_id: str
) -> None:
    store = RunStore(tmp_path)

    with pytest.raises(ValueError):
        store.write_stage(run_id, "extracted", {"value": 1})


@pytest.mark.parametrize(
    ("run_id", "stage_name"),
    [
        ("../escape", "extracted"),
        ("..", "extracted"),
        ("run/1", "extracted"),
        ("run\\1", "extracted"),
        ("C:/absolute", "extracted"),
        ("run-1", "../extracted"),
        ("run-1", "nested/stage"),
        ("run-1", "nested\\stage"),
        ("run-1", "stage..name"),
    ],
)
def test_rejects_run_and_stage_traversal(
    tmp_path: Path, run_id: str, stage_name: str
) -> None:
    store = RunStore(tmp_path)

    if run_id == "run-1":
        store.create_run(
            pdf_hash="abc123",
            rule_version="2026-09-01",
            model_mode="copilot",
            schema_version="1.0",
        )

    with pytest.raises(ValueError):
        store.write_stage(run_id, stage_name, {"value": 1})


def test_stage_write_is_atomic_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_run(
        pdf_hash="abc123",
        rule_version="2026-09-01",
        model_mode="copilot",
        schema_version="1.0",
    )

    original_replace = Path.replace

    def fail_stage_replace(self: Path, target: Path) -> Path:
        if self.name == "extracted.json.tmp":
            raise OSError("disk full")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_stage_replace)

    with pytest.raises(OSError, match="disk full"):
        store.write_stage(manifest.run_id, "extracted", {"value": 1})

    run_dir = tmp_path / ".runs" / manifest.run_id
    assert not (run_dir / "extracted.json").exists()
    assert not (run_dir / "extracted.json.tmp").exists()
    assert (run_dir / "manifest.json").exists()


def test_stage_write_preserves_existing_final_json_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_run(
        pdf_hash="abc123",
        rule_version="2026-09-01",
        model_mode="copilot",
        schema_version="1.0",
    )
    old_payload = {"value": 1, "requirements": [{"requirement_id": "REQ-1"}]}
    new_payload = {"value": 2}

    store.write_stage(manifest.run_id, "extracted", old_payload)

    run_dir = tmp_path / ".runs" / manifest.run_id
    stage_path = run_dir / "extracted.json"
    original_text = stage_path.read_text(encoding="utf-8")
    original_replace = Path.replace

    def fail_stage_replace(self: Path, target: Path) -> Path:
        if self.name == "extracted.json.tmp":
            raise OSError("disk full")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_stage_replace)

    with pytest.raises(OSError, match="disk full"):
        store.write_stage(manifest.run_id, "extracted", new_payload)

    assert stage_path.read_text(encoding="utf-8") == original_text
    assert store.read_stage(manifest.run_id, "extracted") == old_payload
    assert not (run_dir / "extracted.json.tmp").exists()


def test_resume_requires_exact_identity_fields(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_run(
        pdf_hash="abc",
        rule_version="1",
        model_mode=ProviderMode.COPILOT,
        schema_version="1.0",
    )

    assert store.can_resume(manifest.run_id, "abc", "1", "copilot", "1.0")
    assert not store.can_resume(manifest.run_id, "different", "1", "copilot", "1.0")
    assert not store.can_resume(manifest.run_id, "abc", "2", "copilot", "1.0")
    assert not store.can_resume(manifest.run_id, "abc", "1", "local", "1.0")
    assert not store.can_resume(manifest.run_id, "abc", "1", "copilot", "2.0")
    assert store.can_resume(manifest.run_id, "abc", "1", ProviderMode.COPILOT, "1.0")


def test_hash_file_sha256_streaming_matches_hashlib(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    content = (b"0123456789abcdef" * 70000) + b"tail"
    sample.write_bytes(content)

    assert len(content) > 1024 * 1024
    assert _hash_file_sha256(sample) == hashlib.sha256(content).hexdigest()


def test_record_failure_persists_only_summary_and_not_requirement_text(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_run(
        pdf_hash="abc123",
        rule_version="2026-09-01",
        model_mode="copilot",
        schema_version="1.0",
    )
    secret_text = "用户密码重置流程必须记录完整敏感字段"

    store.record_failure(
        manifest.run_id,
        ReviewError(
            code="ANALYSIS_INVALID",
            message="analysis failed",
            details={
                "requirement_id": "REQ-9",
                "rule_id": "rule-7",
                "requirement_text": secret_text,
                "error": "model returned invalid payload",
            },
        ),
    )

    failures_path = tmp_path / ".runs" / manifest.run_id / "failures.json"
    stored_text = failures_path.read_text(encoding="utf-8")
    assert secret_text not in stored_text

    failures = json.loads(stored_text)
    assert failures == [
        {
            "code": "ANALYSIS_INVALID",
            "message": "analysis failed",
            "requirement_id": "REQ-9",
            "rule_id": "rule-7",
            "error": "model returned invalid payload",
        }
    ]