# Fast Requirements Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one Copilot Chat request complete a full PDF review in no more than 15 compact model batches while preserving evidence validation, deterministic scoring, resume, and strict-mode compatibility.

**Architecture:** Add a deterministic logical-requirement grouping stage and a compact verdict contract. Fast runs store logical requirements and a stable byte-budgeted batch plan; Python expands compact verdicts into the existing full analysis/report models. Existing strict tools and run files remain supported.

**Tech Stack:** Python 3.12, Pydantic 2.x, MCP Python SDK 2.x, PyMuPDF, pytest, Ruff, mypy

**Spec:** `docs/superpowers/specs/2026-09-04-fast-requirements-review-design.md`

## Global Constraints

- Fast mode is the default; strict mode remains available and unchanged at its existing MCP tools.
- Every logical requirement and applicable rule receives an explicit validated verdict.
- Evidence is submitted as a source index and expanded locally; unknown indexes are rejected.
- Scores and report models remain deterministic and local.
- No VS Code extension, unofficial Copilot endpoint, OCR, or external service is introduced.
- The reference Ventilation PUMU PDF must prepare in no more than 15 fast batches.
- Existing incomplete strict runs are not migrated.
- Do not commit changes unless the user explicitly requests a commit.

---

### Task 1: Logical Requirement Grouping

**Files:**

- Create: `src/requirements_review_agent/logical_requirements.py`
- Modify: `src/requirements_review_agent/models.py`
- Test: `tests/test_logical_requirements.py`

**Interfaces:**

- Consumes: `tuple[AtomicRequirement, ...]` from the existing normalizer.
- Produces: `LogicalRequirement` and `group_logical_requirements(requirements) -> tuple[LogicalRequirement, ...]`.

- [ ] **Step 1: Add failing grouping tests**

Cover repeated `VESW-1234 - title` sections, ordered multi-source aggregation, standalone normative requirements, filtering standalone descriptive fragments, and stable IDs.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `uv run python -m pytest tests/test_logical_requirements.py -q`

Expected: collection failure because `LogicalRequirement` and `group_logical_requirements` do not exist.

- [ ] **Step 3: Implement the model and deterministic grouper**

`LogicalRequirement` contains `requirement_id`, `title`, `text`, `sources`, `external_id`, and `needs_manual_review`. Group by a source section matching `^(PREFIX-number)\s+-`, preserve source order, include standalone normative requirements, and drop standalone non-normative fragments.

- [ ] **Step 4: Run focused tests**

Run: `uv run python -m pytest tests/test_logical_requirements.py -q`

Expected: all tests pass.

---

### Task 2: Compact Verdict Contract And Expansion

**Files:**

- Create: `src/requirements_review_agent/fast_analysis.py`
- Modify: `src/requirements_review_agent/models.py`
- Test: `tests/test_fast_analysis.py`

**Interfaces:**

- Consumes: logical requirements and `Mapping[str, tuple[ApplicableRule, ...]]`.
- Produces: `FastAnalysisBatch`, `CompactAnalysisSubmission`, `build_fast_batches`, and `expand_compact_submission`.

- [ ] **Step 1: Add failing compact-contract tests**

Test exact requirement IDs, exact rule IDs, source-index bounds, complete verdict evidence, deterministic expansion, scenario derivation, and stable byte-budgeted batching.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `uv run python -m pytest tests/test_fast_analysis.py -q`

Expected: collection failure because the fast-analysis module does not exist.

- [ ] **Step 3: Implement compact input and output models**

Use schema version `2.0`. Batch requirements expose source quotations without geometry. Verdicts contain only status, evidence indexes, optional reason, and confidence. Rule metadata appears once per batch.

- [ ] **Step 4: Implement validation and local expansion**

Expand source indexes to immutable `SourceRef` objects. Derive impact, severity, finding type, Chinese question, rationale, and category scenarios from local rule data. Reject omissions and unknown IDs.

- [ ] **Step 5: Implement conservative serialized-byte batching**

Pack whole logical requirements without splitting. Use a configurable byte ceiling and stable batch IDs derived from ordered requirement IDs and serialized content.

- [ ] **Step 6: Run focused tests**

Run: `uv run python -m pytest tests/test_fast_analysis.py -q`

Expected: all tests pass.

---

### Task 3: Fast Review Service State Machine

**Files:**

- Modify: `src/requirements_review_agent/service.py`
- Modify: `src/requirements_review_agent/models.py`
- Test: `tests/test_service.py`

**Interfaces:**

- Extends: `ReviewService.prepare(..., review_mode=ReviewMode.FAST)`.
- Produces: `ReviewService.get_next_fast_batch(run_id)` and `ReviewService.submit_fast(run_id, batch_id, submission)`.

- [ ] **Step 1: Add failing fast round-trip and resume tests**

Test default fast preparation, stored mode and batch plan, first-incomplete retrieval, idempotent same-payload submission, conflicting duplicate rejection, state persistence across service instances, merge to existing full submission, and final report generation.

- [ ] **Step 2: Run the focused service tests and verify failure**

Run: `uv run python -m pytest tests/test_service.py -q`

Expected: failures for missing review mode and fast methods.

- [ ] **Step 3: Implement fast preparation**

Store logical requirements in the existing requirements stage using their compatible fields, select rules, create and persist `fast_batches.json`, and record `review_mode`, compact schema version, and batch IDs in service state.

- [ ] **Step 4: Implement next-batch and idempotent submit**

Return the first incomplete batch. Persist each expanded `RequirementAnalysis` under a batch-specific stage before updating state. Repeated identical submissions return current status; conflicting content fails.

- [ ] **Step 5: Merge completed fast submissions**

Create the existing full `AnalysisSubmission`, validate it with current validators, set stage to analyzed, and reuse unchanged scoring and reporting.

- [ ] **Step 6: Run service tests**

Run: `uv run python -m pytest tests/test_service.py -q`

Expected: all strict and fast tests pass.

---

### Task 4: MCP And Agent Workflow

**Files:**

- Modify: `src/requirements_review_agent/server.py`
- Modify: `.github/agents/requirements-review.agent.md`
- Modify: `tests/test_server.py`
- Modify: `tests/test_e2e.py`

**Interfaces:**

- Extends: `prepare_review` with `review_mode` defaulting to `fast`.
- Adds: `get_next_review_batch(run_id)` and `submit_review_verdicts(run_id, batch_id, submission)`.

- [ ] **Step 1: Add failing MCP schema and end-to-end tests**

Assert fast mode defaults, compact tool schemas, a complete prepare/next/submit/finalize flow, and continued availability of strict tools.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run python -m pytest tests/test_server.py tests/test_e2e.py -q`

Expected: failures for missing fast tools and workflow instructions.

- [ ] **Step 3: Add thin MCP adapters**

Expose the service methods with structured Pydantic input and output. Do not place batching, validation, or persistence logic in `server.py`.

- [ ] **Step 4: Simplify the Agent workflow**

Default to automotive fast review when the user requests ECU analysis, repeatedly request the next incomplete batch, submit compact verdicts, and finalize when `done=true`. Explicit strict mode uses the legacy tools.

- [ ] **Step 5: Run focused tests**

Run: `uv run python -m pytest tests/test_server.py tests/test_e2e.py -q`

Expected: all tests pass.

---

### Task 5: Documentation And Acceptance Verification

**Files:**

- Modify: `README.md`
- Test: full suite and reference PDF preparation

**Interfaces:**

- Documents: default fast mode, optional strict mode, compact resume behavior, and metric definitions.

- [ ] **Step 1: Update user documentation**

Show one-request fast usage for automotive ECU PDFs, strict-mode opt-in, resume semantics, and the distinction between requirement metrics and real test coverage.

- [ ] **Step 2: Run the full automated verification**

Run:

```powershell
uv run python -m pytest -q
uv run ruff check .
uv run mypy src
uv run rra doctor
```

Expected: all commands exit zero.

- [ ] **Step 3: Prepare the reference PDF in fast mode**

Run the service against `requirements/Software_System_Documentation_-_VE_ECU_Components_Ventilation_PUMU_Software_Specification (2).pdf` with `automotive-ecu-v1` and assert `batch_count <= 15`.

- [ ] **Step 4: Inspect final diff and report limitations**

Confirm no unrelated files were modified and report any remaining need for manual Copilot quota consent or a live-model smoke test.
