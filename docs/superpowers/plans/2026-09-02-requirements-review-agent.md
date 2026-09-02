# Requirements Review Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a portable VS Code Copilot custom agent backed by a local Python MCP server that reviews text/table PDFs, produces evidence-linked findings and deterministic metrics, and exports JSON, Markdown, and Word `.docx` reports.

**Architecture:** A framework-independent `ReviewService` owns the workflow and composes extraction, normalization, rules, analysis validation, scoring, storage, and reporting. A thin MCP stdio server exposes that service to Copilot Chat; Copilot-mode analysis is submitted back through MCP, while company/local OpenAI-compatible endpoints use an adapter implementing the same contract. Pydantic models form the single data contract and final JSON remains the source for both report renderers.

**Tech Stack:** Python 3.12, uv, MCP Python SDK 2.x, Pydantic 2.x, PyMuPDF 1.28.x, PyYAML 6.x, HTTPX 0.28.x, python-docx 1.2.x, pytest 8.x, pytest-asyncio 1.x, Ruff 0.12+, mypy 1.17+

**Spec:** `docs/superpowers/specs/2026-09-01-requirements-review-agent-design.md`

## Global Constraints

- Support text and table PDFs only; do not invoke OCR and reject pages with no extractable text.
- Report prose and questions are Chinese; preserve source quotations and technical/protocol/interface/state names in their original language.
- Never label either deterministic metric as real test-case or execution coverage.
- Every factual finding and covered scenario must cite a valid source reference from the extracted document.
- Final scores are computed only by local deterministic code; providers never submit score fields.
- JSON is the only report source; Markdown and `.docx` render from the validated `ReviewReport` model.
- Store runs only below workspace-local `.runs/`; never log full requirement text or credentials.
- External endpoints require explicit provider mode and credentials from environment variables; no secret appears in committed configuration.
- The stdio MCP server writes protocol data only to stdout; all logs go to stderr.
- Tests use synthetic or approved sanitized documents only.

## File Structure

```text
.github/agents/requirements-review.agent.md       Copilot workflow and safety instructions
.vscode/mcp.json                                  Portable stdio MCP launch configuration
.env.example                                      Non-secret provider variable names
.gitignore                                        Ignore runs, environment, and local inputs
README.md                                         Windows setup and usage
pyproject.toml                                    Package, dependencies, tools, entry point
uv.lock                                           Reproducible dependency lock
schemas/analysis-submission.schema.json           Checked-in provider/Agent contract
rules/home-iot-v1.yaml                            Default appliance/embedded/IoT checks
src/requirements_review_agent/models.py           All Pydantic data contracts and enums
src/requirements_review_agent/errors.py           Stable public error codes
src/requirements_review_agent/pdf_extractor.py    PDF validation, text/table/source extraction
src/requirements_review_agent/normalizer.py       Deterministic requirement splitting and IDs
src/requirements_review_agent/rules.py            YAML rule loading and applicability
src/requirements_review_agent/scoring.py          Deterministic metric calculation
src/requirements_review_agent/storage.py          Run manifest, atomic persistence, resume
src/requirements_review_agent/providers/base.py   Provider protocol and request model
src/requirements_review_agent/providers/openai.py OpenAI-compatible company/local adapter
src/requirements_review_agent/analysis.py         Batch construction, validation, retries
src/requirements_review_agent/reporting.py        JSON, Markdown, and DOCX renderers
src/requirements_review_agent/service.py          Framework-independent use cases
src/requirements_review_agent/server.py           MCP tools and stdio entry point
tests/fixtures/build_pdfs.py                       Synthetic PDF fixture factory
tests/fixtures/provider_submission.json            Valid mixed-language model response
tests/test_models.py                              Contract and JSON Schema tests
tests/test_pdf_extractor.py                       Extraction and failure tests
tests/test_normalizer.py                          Stable splitting tests
tests/test_rules.py                               Rule-pack tests
tests/test_scoring.py                             Formula and evidence tests
tests/test_storage.py                             Resume and atomic-write tests
tests/test_providers.py                           Provider contract/retry tests
tests/test_reporting.py                           Cross-format consistency tests
tests/test_service.py                             Workflow tests
tests/test_server.py                              In-process MCP tool tests
tests/test_e2e.py                                 Synthetic PDF to three reports
```

---

### Task 1: Project Foundation and Domain Contract

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/requirements_review_agent/__init__.py`
- Create: `src/requirements_review_agent/models.py`
- Create: `src/requirements_review_agent/errors.py`
- Create: `schemas/analysis-submission.schema.json`
- Test: `tests/test_models.py`

**Interfaces:**

- Consumes: none.
- Produces: all shared enums and Pydantic contracts: `ProviderMode`, `SourceRef`, `ExtractedTable`, `ExtractedPage`, `ExtractedDocument`, `AtomicRequirement`, `RuleCheck`, `ApplicableRule`, `CheckResult`, `ScenarioResult`, `RequirementAnalysis`, `AnalysisSubmission`, `RequirementScore`, `AggregateScore`, `RequirementReview`, `ReviewReport`, `RunManifest`, `PreparedReview`, `RunStatus`, `ReportArtifacts`, `ReviewError`, and `ReviewException`.

- [ ] **Step 1: Create package metadata and test configuration**

Create `pyproject.toml` with this dependency surface and console entry point:

```toml
[project]
name = "requirements-review-agent"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = [
  "httpx>=0.28,<0.29",
  "mcp[cli]>=2.1,<3",
  "pydantic>=2.11,<3",
  "pymupdf>=1.28,<1.29",
  "python-docx>=1.2,<2",
  "pyyaml>=6.0,<7",
]

[project.scripts]
requirements-review-mcp = "requirements_review_agent.server:main"

[dependency-groups]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "ruff>=0.12,<1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/requirements_review_agent"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["requirements_review_agent"]
```

Add `.runs/`, `.venv/`, `.env`, `inputs/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, and `.ruff_cache/` to `.gitignore`. Run `uv lock` after creating the files.

- [ ] **Step 2: Write failing contract tests**

```python
from pydantic import ValidationError
import pytest

from requirements_review_agent.models import (
    AnalysisSubmission,
    CheckResult,
    CheckStatus,
    Impact,
    RequirementAnalysis,
    Severity,
    SourceRef,
)


def test_analysis_contract_forbids_unknown_and_score_fields() -> None:
    payload = {
        "schema_version": "1.0",
        "requirements": [{
            "requirement_id": "REQ-a1b2c3d4",
            "checks": [{
                "rule_id": "behavior.acceptance",
                "status": "missing",
                "impact": "both",
                "severity": "blocking",
                "finding_type": "suggestion",
                "evidence": [],
                "rationale": "未定义成功条件",
                "question": "成功条件是什么？",
                "confidence": 0.9,
            }],
            "scenarios": [],
            "score": 100,
        }],
    }
    with pytest.raises(ValidationError):
        AnalysisSubmission.model_validate(payload)


def test_source_reference_requires_one_based_page() -> None:
    with pytest.raises(ValidationError):
        SourceRef(page=0, quote="Start")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`

Expected: FAIL during import because `requirements_review_agent.models` does not exist.

- [ ] **Step 4: Implement strict Pydantic contracts and stable errors**

Use `ConfigDict(extra="forbid", frozen=True)` on persisted models. Define string enums exactly as follows:

```python
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
```

`ProviderMode` values are `copilot`, `company_api`, and `local`. `SourceRef` has `page: int = Field(ge=1)`, `section: str | None`, `table_index: int | None = Field(default=None, ge=0)`, `bbox: tuple[float, float, float, float] | None`, and non-empty `quote`. `ExtractedTable` owns copied cell strings and coordinates; `ExtractedPage` owns text blocks and tables; `ExtractedDocument` owns the input SHA-256 and pages. `AtomicRequirement` owns stable ID, normalized text, source tuple, and `needs_manual_review`.

`CheckResult` and `ScenarioResult` carry evidence lists; `RequirementAnalysis` contains no score. `RequirementReview` contains one validated analysis plus its `RequirementScore`. `ReviewReport` contains schema version, run metadata, `tuple[RequirementReview, ...]`, `AggregateScore`, and failures. `ReportArtifacts` contains JSON/Markdown paths, optional DOCX path, and `complete` or `partial` status. `PreparedReview` and `RunStatus` expose only run metadata, counts, warnings, stage, and artifact paths. Add model validators requiring fact findings and covered scenarios to have evidence. Define `ReviewError(code: str, message: str, details: dict[str, object])`; `ReviewException(Exception)` owns one `ReviewError` and includes its code in `str(exception)`. Define exception codes `PDF_ENCRYPTED`, `PDF_DAMAGED`, `PDF_SCANNED`, `PDF_OUTSIDE_WORKSPACE`, `RULE_PACK_INVALID`, `ANALYSIS_INVALID`, `PROVIDER_UNAVAILABLE`, and `REPORT_PARTIAL`.

Generate the checked-in schema from `AnalysisSubmission.model_json_schema()` using sorted keys and a trailing newline.

- [ ] **Step 5: Verify contract, lint, and types**

Run: `uv run pytest tests/test_models.py -v && uv run ruff check . && uv run mypy src`

Expected: all commands PASS.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml uv.lock .gitignore src tests/test_models.py schemas
git commit -m "feat: define review domain contract"
```

---

### Task 2: PDF Extraction and Stable Requirement Normalization

**Files:**

- Create: `src/requirements_review_agent/pdf_extractor.py`
- Create: `src/requirements_review_agent/normalizer.py`
- Create: `tests/fixtures/build_pdfs.py`
- Test: `tests/test_pdf_extractor.py`
- Test: `tests/test_normalizer.py`

**Interfaces:**

- Consumes: `SourceRef`, `ExtractedDocument`, `AtomicRequirement` from Task 1.
- Produces: `extract_pdf(path: Path, workspace: Path) -> ExtractedDocument`; `normalize_requirements(document: ExtractedDocument) -> tuple[AtomicRequirement, ...]`.

- [ ] **Step 1: Write synthetic PDF fixtures and failing extraction tests**

Build fixtures at test runtime with PyMuPDF, using `page.insert_textbox(...)` for paragraphs and `page.draw_rect`/`page.draw_line` plus cell text for a two-column table. Include separate fixtures for text+table, an empty image-only page, and encrypted PDF.

```python
def test_extracts_text_table_and_one_based_sources(tmp_path: Path) -> None:
    pdf = build_text_table_pdf(tmp_path / "mixed.pdf")
    result = extract_pdf(pdf, tmp_path)
    assert result.pages[0].page == 1
    assert "Wi-Fi" in result.pages[0].text
    assert result.pages[0].tables[0].cells[1] == ["Timeout", "30 s"]


def test_rejects_scanned_or_empty_page(tmp_path: Path) -> None:
    pdf = build_image_only_pdf(tmp_path / "scan.pdf")
    with pytest.raises(ReviewException, match="PDF_SCANNED"):
        extract_pdf(pdf, tmp_path)
```

- [ ] **Step 2: Run extraction tests to verify failure**

Run: `uv run pytest tests/test_pdf_extractor.py -v`

Expected: FAIL because `extract_pdf` is undefined.

- [ ] **Step 3: Implement bounded PDF extraction**

Resolve both input and workspace paths and require `input_path.is_relative_to(workspace)`. Open with `pymupdf.open`; reject `document.needs_pass`, map open errors to `PDF_DAMAGED`, and never call `get_textpage_ocr`. For each page:

```python
blocks = page.get_text("dict", sort=True)["blocks"]
text = "\n".join(
    span["text"]
    for block in blocks if block.get("type") == 0
    for line in block.get("lines", [])
    for span in line.get("spans", []) if span.get("text", "").strip()
)
tables = [
    ExtractedTable(
        page=page.number + 1,
        table_index=index,
        bbox=tuple(table.bbox),
        cells=[list(row) for row in table.extract()],
        needs_manual_review=any(cell is None for row in table.extract() for cell in row),
    )
    for index, table in enumerate(page.find_tables().tables)
]
```

Treat any page with fewer than 20 non-whitespace characters and at least one displayed image as unsupported scanned content. Persist block bounding boxes and quotes before the page object leaves scope because table objects share the page lifetime.

- [ ] **Step 4: Write failing normalizer tests**

```python
def test_normalizer_splits_numbered_items_and_has_stable_ids() -> None:
    document = extracted_document_with(
        "1. Hood shall enter Boost mode.\n2. App displays filter status."
    )
    first = normalize_requirements(document)
    second = normalize_requirements(document)
    assert [item.requirement_id for item in first] == [item.requirement_id for item in second]
    assert len(first) == 2
    assert first[0].sources[0].page == 1
```

- [ ] **Step 5: Implement deterministic normalization**

Split on numbered/bulleted lines and table rows, carry the nearest detected heading, and keep an unsplit paragraph when no deterministic delimiter exists. Compute IDs as `REQ-` plus the first 12 hex characters of SHA-256 over normalized source coordinates and text:

```python
identity = f"{source.page}|{source.section or ''}|{source.table_index}|{normalized_text}"
requirement_id = f"REQ-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"
```

Do not use the model for requirement splitting in v1. Mark ambiguous multi-sentence blocks with `needs_manual_review=True` instead of silently dropping them.

- [ ] **Step 6: Run focused and module tests**

Run: `uv run pytest tests/test_pdf_extractor.py tests/test_normalizer.py -v`

Expected: PASS, including encrypted, scanned, damaged, out-of-workspace, table, and stable-ID cases.

- [ ] **Step 7: Commit**

```powershell
git add src/requirements_review_agent/pdf_extractor.py src/requirements_review_agent/normalizer.py tests/fixtures tests/test_pdf_extractor.py tests/test_normalizer.py
git commit -m "feat: extract and normalize PDF requirements"
```

---

### Task 3: Versioned Home and IoT Rule Pack

**Files:**

- Create: `src/requirements_review_agent/rules.py`
- Create: `rules/home-iot-v1.yaml`
- Test: `tests/test_rules.py`

**Interfaces:**

- Consumes: `AtomicRequirement`, `RuleCheck`, `ApplicableRule`.
- Produces: `load_rule_pack(path: Path) -> RulePack`; `select_applicable_rules(requirement: AtomicRequirement, pack: RulePack) -> tuple[ApplicableRule, ...]`.

- [ ] **Step 1: Write failing rule tests**

```python
def test_wifi_requirement_selects_connectivity_and_recovery_rules() -> None:
    pack = load_rule_pack(Path("rules/home-iot-v1.yaml"))
    rules = select_applicable_rules(requirement("App connects through Wi-Fi"), pack)
    assert {rule.rule_id for rule in rules} >= {
        "connectivity.preconditions",
        "recovery.connection_loss",
        "automation.observability",
    }


def test_invalid_weight_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: '1.0'\nrules:\n- id: bad\n  weight: 0\n", encoding="utf-8")
    with pytest.raises(ReviewException, match="RULE_PACK_INVALID"):
        load_rule_pack(path)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_rules.py -v`

Expected: FAIL because the rule loader does not exist.

- [ ] **Step 3: Implement YAML model and applicability**

Each YAML rule has `id`, Chinese `question`, positive integer `weight`, `impact`, `scenario_category`, `always`, and case-insensitive `keywords`. Include checks for behavior/acceptance, preconditions/state, boundaries/invalid inputs, timeout/retry/recovery, device-App-Cloud/BLE/Wi-Fi dependencies, permissions/security/privacy, performance/compatibility, and automation interface/data/observability/reset.

Applicability is deterministic: include `always: true` rules and keyword matches; preserve file order; deduplicate by ID. Validate with Pydantic before use and require unique IDs, non-empty questions, and at least one `always` rule.

```python
def select_applicable_rules(
    requirement: AtomicRequirement,
    pack: RulePack,
) -> tuple[ApplicableRule, ...]:
    text = requirement.text.casefold()
    selected = []
    for rule in pack.rules:
        if rule.always or any(keyword.casefold() in text for keyword in rule.keywords):
            selected.append(ApplicableRule.model_validate(rule.model_dump()))
    return tuple({rule.rule_id: rule for rule in selected}.values())
```

Require the default pack to contain at least one always-applicable check with a non-null scenario category so both metric denominators are defined for every normalized requirement.

- [ ] **Step 4: Verify rule behavior**

Run: `uv run pytest tests/test_rules.py -v && uv run ruff check src/requirements_review_agent/rules.py tests/test_rules.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add rules src/requirements_review_agent/rules.py tests/test_rules.py
git commit -m "feat: add versioned home IoT review rules"
```

---

### Task 4: Submission Validation and Deterministic Scoring

**Files:**

- Create: `src/requirements_review_agent/analysis.py`
- Create: `src/requirements_review_agent/scoring.py`
- Create: `tests/fixtures/provider_submission.json`
- Test: `tests/test_scoring.py`

**Interfaces:**

- Consumes: `AtomicRequirement`, `ApplicableRule`, `AnalysisSubmission`.
- Produces: `AnalysisBatch`; `build_analysis_batch(...) -> AnalysisBatch`; `validate_submission(submission, requirements, applicable) -> tuple[RequirementAnalysis, ...]`; `score_requirements(analyses, applicable) -> tuple[RequirementScore, ...]`; `aggregate_scores(scores) -> AggregateScore`.

- [ ] **Step 1: Write failing validation and formula tests**

```python
def test_score_uses_only_applicable_weights() -> None:
    applicable = rules(weighted={"acceptance": 2, "timeout": 1})
    analysis = analyzed(checks={"acceptance": "complete", "timeout": "missing"})
    score = score_requirements((analysis,), applicable)[0]
    assert score.testability == pytest.approx(66.67)


def test_scenario_is_covered_only_with_valid_evidence() -> None:
    analysis = analyzed(scenarios=[scenario("recovery", covered=True, evidence=[])])
    with pytest.raises(ReviewException, match="ANALYSIS_INVALID"):
        validate_submission(submission(analysis), requirements(), applicable_rules())


def test_zero_applicable_rules_becomes_configuration_error() -> None:
    with pytest.raises(ReviewException, match="RULE_PACK_INVALID"):
        score_requirements((analyzed(),), {})
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_scoring.py -v`

Expected: FAIL because validation and scoring functions are undefined.

- [ ] **Step 3: Implement cross-reference validation**

Reject duplicate or unknown requirement IDs, missing requirement analyses, duplicate/unknown rule IDs, source references whose `(page, section, table_index, quote)` are not present on the requirement, fact findings without evidence, and covered scenarios without evidence. Require missing or needs-confirmation checks to contain a Chinese question; allow original English technical terms inside it.

```python
expected_ids = {requirement.requirement_id for requirement in requirements}
submitted_ids = {analysis.requirement_id for analysis in submission.requirements}
if submitted_ids != expected_ids:
    raise review_exception("ANALYSIS_INVALID", "分析结果与需求集合不一致")
for analysis in submission.requirements:
    valid_sources = set(requirement_by_id[analysis.requirement_id].sources)
    if any(source not in valid_sources for check in analysis.checks for source in check.evidence):
        raise review_exception("ANALYSIS_INVALID", "结论引用了未知原文证据")
```

- [ ] **Step 4: Implement deterministic scores**

Use `Decimal` and `ROUND_HALF_UP` to two decimal places:

```python
eligible = [rule for rule in rules if result_by_rule[rule.rule_id].status != NOT_APPLICABLE]
numerator = sum(rule.weight for rule in eligible if result_by_rule[rule.rule_id].status == COMPLETE)
testability = percent(numerator, sum(rule.weight for rule in eligible))

categories = {rule.scenario_category for rule in rules if rule.scenario_category}
if not categories:
    raise review_exception("RULE_PACK_INVALID", "需求没有适用的场景类别")
covered = {item.category for item in analysis.scenarios if item.covered and item.evidence}
scenario_coverage = percent(len(covered & categories), len(categories))
```

Aggregate by equal arithmetic mean over requirements. Exclude only the rule-configuration error case, which must fail rather than silently lower the denominator.

- [ ] **Step 5: Verify deterministic output**

Make the deterministic test call `score_requirements` twice in the same process and compare both serialized outputs to the golden JSON.

Run: `uv run pytest tests/test_scoring.py -v`

Expected: PASS; both calculations match byte-for-byte without requiring a repeat-test plugin.

- [ ] **Step 6: Commit**

```powershell
git add src/requirements_review_agent/analysis.py src/requirements_review_agent/scoring.py tests/fixtures/provider_submission.json tests/test_scoring.py
git commit -m "feat: validate findings and calculate review metrics"
```

---

### Task 5: Atomic Run Storage and Resume Rules

**Files:**

- Create: `src/requirements_review_agent/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**

- Consumes: all persisted Pydantic models.
- Produces: `RunStore(workspace: Path)` with `create_run`, `write_stage`, `read_stage`, `can_resume`, and `record_failure`.

- [ ] **Step 1: Write failing storage tests**

```python
def test_resume_requires_all_identity_fields(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_run(pdf_hash="abc", rule_version="1", model_mode="copilot", schema_version="1.0")
    assert store.can_resume(manifest.run_id, "abc", "1", "copilot", "1.0")
    assert not store.can_resume(manifest.run_id, "abc", "2", "copilot", "1.0")


def test_stage_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore(tmp_path)
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        store.write_stage("run-1", "extracted", {"value": 1})
    assert not (tmp_path / ".runs/run-1/extracted.json").exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_storage.py -v`

Expected: FAIL because `RunStore` is undefined.

- [ ] **Step 3: Implement local-only run storage**

Create `.runs/<UTC timestamp>-<8 hex>/manifest.json`. Resolve all paths and assert each remains below `.runs`. Write JSON through a sibling `.tmp` file, flush and `os.fsync`, then `Path.replace`. Store only IDs and error summaries in `failures.json`; stage files may contain extracted requirement content but logs must not. Compute input SHA-256 by streaming 1 MiB chunks.

```python
def _write_json_atomic(path: Path, value: BaseModel | dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
```

- [ ] **Step 4: Verify resume and traversal defenses**

Run: `uv run pytest tests/test_storage.py -v`

Expected: PASS, including a test that rejects run IDs containing `..` or separators.

- [ ] **Step 5: Commit**

```powershell
git add src/requirements_review_agent/storage.py tests/test_storage.py
git commit -m "feat: persist resumable review runs"
```

---

### Task 6: Provider Contract and Explicit Model Modes

**Files:**

- Create: `src/requirements_review_agent/providers/__init__.py`
- Create: `src/requirements_review_agent/providers/base.py`
- Create: `src/requirements_review_agent/providers/openai.py`
- Test: `tests/test_providers.py`

**Interfaces:**

- Consumes: analysis batches generated by `build_analysis_batch(...)` in `analysis.py`.
- Produces: `AnalysisProvider` protocol with `async analyze(batch: AnalysisBatch) -> AnalysisSubmission`; `OpenAICompatibleProvider`; `build_provider(mode: ProviderMode, env: Mapping[str, str]) -> AnalysisProvider | None`.

- [ ] **Step 1: Write failing provider tests**

```python
async def test_copilot_mode_returns_no_server_side_provider() -> None:
    assert build_provider(ProviderMode.COPILOT, {}) is None


async def test_external_mode_requires_explicit_endpoint_and_env_key() -> None:
    with pytest.raises(ReviewException, match="PROVIDER_UNAVAILABLE"):
        build_provider(ProviderMode.COMPANY_API, {})


async def test_adapter_parses_schema_response(respx_mock: MockRouter) -> None:
    respx_mock.post("https://approved.example/v1/chat/completions").mock(
        return_value=Response(200, json=openai_response(valid_submission_json()))
    )
    result = await provider().analyze(batch())
    assert result.schema_version == "1.0"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_providers.py -v`

Expected: FAIL because the provider package does not exist. Add `respx>=0.22,<1` to the dev dependency group and refresh `uv.lock` when implementing the HTTP test.

- [ ] **Step 3: Implement provider protocol and OpenAI-compatible adapter**

Use environment names `RRA_COMPANY_BASE_URL`, `RRA_COMPANY_API_KEY`, `RRA_COMPANY_MODEL`, `RRA_LOCAL_BASE_URL`, and `RRA_LOCAL_MODEL`. Local mode permits no key; company mode requires one. Send `response_format` JSON Schema when supported and parse the returned content with `AnalysisSubmission.model_validate_json`.

Set connect timeout 10 seconds, total timeout 120 seconds, maximum two retries for timeout/429/5xx only, and exponential delays of 1 and 2 seconds through an injected `sleep` callable so tests do not wait. Never retry 4xx validation/auth failures. Redact headers and request bodies from exception messages.

```python
class AnalysisProvider(Protocol):
    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission: ...


class OpenAICompatibleProvider:
    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission:
        response = await self._client.post(
            "/v1/chat/completions",
            headers=self._authorization_header(),
            json=self._request_payload(batch),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return AnalysisSubmission.model_validate_json(content)
```

- [ ] **Step 4: Verify adapters and offline fake**

Add a test-only `FakeProvider` implementing the same protocol, then run:

Run: `uv run pytest tests/test_providers.py -v`

Expected: PASS without real network or credentials.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml uv.lock src/requirements_review_agent/providers tests/test_providers.py
git commit -m "feat: add explicit model provider adapters"
```

---

### Task 7: Reports from One Validated JSON Source

**Files:**

- Create: `src/requirements_review_agent/reporting.py`
- Test: `tests/test_reporting.py`

**Interfaces:**

- Consumes: `ReviewReport` only.
- Produces: `write_json(report, path)`, `write_markdown(report, path)`, `write_docx(report, path)`, and `render_all(report, output_dir) -> ReportArtifacts`.

- [ ] **Step 1: Write failing cross-format report tests**

```python
def test_all_formats_contain_same_ids_scores_and_failures(tmp_path: Path) -> None:
    artifacts = render_all(review_report(), tmp_path)
    json_data = json.loads(artifacts.json.read_text(encoding="utf-8"))
    markdown = artifacts.markdown.read_text(encoding="utf-8")
    document = Document(artifacts.docx)
    docx_text = "\n".join(p.text for p in document.paragraphs)
    assert json_data["requirements"][0]["requirement_id"] in markdown in docx_text
    assert "建议场景覆盖度（不是真实测试覆盖率）" in markdown
    assert "Wi-Fi" in docx_text


def test_docx_failure_returns_partial_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("requirements_review_agent.reporting.write_docx", Mock(side_effect=OSError))
    result = render_all(review_report(), tmp_path)
    assert result.status == "partial"
    assert result.json.exists() and result.markdown.exists() and result.docx is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_reporting.py -v`

Expected: FAIL because report renderers do not exist.

- [ ] **Step 3: Implement JSON and Markdown renderers**

Serialize with `report.model_dump(mode="json")`, UTF-8, `ensure_ascii=False`, sorted keys, two-space indentation, and atomic writes. Markdown sections must be: 执行摘要, 指标定义, 阻塞问题, 需求评审矩阵, 手动测试缺失信息, 自动化测试缺失信息, 建议场景, 未完成项, 运行元数据. Print evidence as `p.<page> / <section>: “<quote>”` and never translate the quote.

- [ ] **Step 4: Implement DOCX renderer**

Use `Document()`, `add_heading`, `add_paragraph`, and `add_table`; set core language to `zh-CN`; add tables incrementally to avoid nesting cards/tables. Keep technical strings exactly as held in JSON. Save to a temporary `.docx` and replace atomically. Catch only DOCX rendering errors in `render_all`, preserve JSON/Markdown, and append a `REPORT_PARTIAL` failure.

```python
def render_all(report: ReviewReport, output_dir: Path) -> ReportArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review.json"
    markdown_path = output_dir / "review.md"
    docx_path = output_dir / "review.docx"
    try:
        write_docx(report, docx_path)
    except (OSError, PackageNotFoundError) as error:
        report = report.model_copy(update={
            "failures": (*report.failures, report_failure("REPORT_PARTIAL", error))
        })
        write_json(report, json_path)
        write_markdown(report, markdown_path)
        return ReportArtifacts(json=json_path, markdown=markdown_path, docx=None, status="partial")
    write_json(report, json_path)
    write_markdown(report, markdown_path)
    return ReportArtifacts(json=json_path, markdown=markdown_path, docx=docx_path, status="complete")
```

- [ ] **Step 5: Verify format consistency**

Run: `uv run pytest tests/test_reporting.py -v`

Expected: PASS and generated `.docx` reopens with `Document(path)`.

- [ ] **Step 6: Commit**

```powershell
git add src/requirements_review_agent/reporting.py tests/test_reporting.py
git commit -m "feat: render review reports from validated JSON"
```

---

### Task 8: Review Service and MCP Tool Surface

**Files:**

- Create: `src/requirements_review_agent/service.py`
- Create: `src/requirements_review_agent/server.py`
- Test: `tests/test_service.py`
- Test: `tests/test_server.py`

**Interfaces:**

- Consumes: all earlier module interfaces.
- Produces: `ReviewService.prepare`, `get_batch`, `submit`, `run_provider`, `finalize`, `status`; MCP tools with matching snake_case names.

- [ ] **Step 1: Write failing service workflow tests**

```python
def test_copilot_workflow_requires_submission_before_finalize(service: ReviewService, pdf: Path) -> None:
    prepared = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    assert prepared.data_destination == "GitHub Copilot model selected in VS Code"
    with pytest.raises(ReviewException, match="ANALYSIS_INVALID"):
        service.finalize(prepared.run_id)
    service.submit(prepared.run_id, valid_submission())
    artifacts = service.finalize(prepared.run_id)
    assert artifacts.json.exists()


async def test_local_mode_uses_injected_provider(service_with_fake_provider: ReviewService, pdf: Path) -> None:
    prepared = service_with_fake_provider.prepare(pdf, "home-iot-v1", ProviderMode.LOCAL)
    await service_with_fake_provider.run_provider(prepared.run_id)
    assert service_with_fake_provider.status(prepared.run_id).stage == "analyzed"
```

- [ ] **Step 2: Run service tests to verify failure**

Run: `uv run pytest tests/test_service.py -v`

Expected: FAIL because `ReviewService` does not exist.

- [ ] **Step 3: Implement the application service state machine**

Allowed stages are `prepared -> analyzed -> finalized`, plus `partial` and `failed`. `prepare` hashes the input, extracts, normalizes, selects rules, stores all stage data, and returns run ID, provider/data destination, requirement count, warnings, and batch count. `get_batch` returns requirements, source evidence, applicable checks, Chinese output instructions, and the exact `AnalysisSubmission` schema. `submit` validates before storage. `run_provider` is illegal in Copilot mode. `finalize` scores and renders only validated submissions. `status` returns counts and artifact paths without returning full requirement text.

```python
class ReviewService:
    def prepare(self, pdf: Path, rule_pack: str, mode: ProviderMode) -> PreparedReview: ...
    def get_batch(self, run_id: str, batch_index: int) -> AnalysisBatch: ...
    def submit(self, run_id: str, submission: AnalysisSubmission) -> RunStatus: ...
    async def run_provider(self, run_id: str) -> RunStatus: ...
    def finalize(self, run_id: str) -> ReportArtifacts: ...
    def status(self, run_id: str) -> RunStatus: ...
```

- [ ] **Step 4: Write failing MCP registration tests**

```python
async def test_server_lists_only_review_tools(mcp_client: Client) -> None:
    names = {tool.name for tool in (await mcp_client.list_tools()).tools}
    assert names == {
        "prepare_review",
        "get_analysis_batch",
        "submit_analysis",
        "run_provider_analysis",
        "finalize_review",
        "get_review_status",
    }
```

- [ ] **Step 5: Implement MCPServer 2.x wrapper**

Create `mcp = MCPServer("requirements-review")`, register six typed `@mcp.tool()` functions, and have each call a module-level service factory. Return structured Pydantic-compatible dictionaries, not prose-only strings. In `main()`, configure `logging.basicConfig(stream=sys.stderr)` and call `mcp.run(transport="stdio")`; never call `print()`.

```python
mcp = MCPServer("requirements-review")


@mcp.tool()
def prepare_review(pdf_path: str, rule_pack: str, model_mode: ProviderMode) -> dict[str, object]:
    return get_service().prepare(Path(pdf_path), rule_pack, model_mode).model_dump(mode="json")


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mcp.run(transport="stdio")
```

- [ ] **Step 6: Verify service and MCP transport**

Run: `uv run pytest tests/test_service.py tests/test_server.py -v`

Expected: PASS using the SDK in-process client/transport; no subprocess stdout contains non-JSON-RPC text.

- [ ] **Step 7: Commit**

```powershell
git add src/requirements_review_agent/service.py src/requirements_review_agent/server.py tests/test_service.py tests/test_server.py
git commit -m "feat: expose review workflow through MCP"
```

---

### Task 9: Copilot Agent, Portable Configuration, and End-to-End Acceptance

**Files:**

- Create: `.github/agents/requirements-review.agent.md`
- Create: `.vscode/mcp.json`
- Create: `.env.example`
- Create: `README.md`
- Create: `tests/test_e2e.py`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: six MCP tools from Task 8.
- Produces: discoverable workspace agent, reproducible Windows setup, and a complete synthetic-PDF acceptance path.

- [ ] **Step 1: Write the failing end-to-end test**

```python
def test_text_table_pdf_produces_consistent_local_artifacts(tmp_path: Path) -> None:
    assert Path(".github/agents/requirements-review.agent.md").exists()
    assert Path(".vscode/mcp.json").exists()
    pdf = build_text_table_pdf(tmp_path / "input.pdf")
    service = review_service(tmp_path)
    run = service.prepare(pdf, "home-iot-v1", ProviderMode.COPILOT)
    service.submit(run.run_id, submission_for(run))
    artifacts = service.finalize(run.run_id)
    report = ReviewReport.model_validate_json(artifacts.json.read_text(encoding="utf-8"))
    assert artifacts.markdown.exists() and artifacts.docx.exists()
    assert report.aggregate.testability >= 0
    assert all(
        check.evidence
        for item in report.requirements
        for check in item.analysis.checks
        if check.finding_type is FindingType.FACT
    )
    assert set(type(report.aggregate).model_fields) == {"testability", "scenario_coverage"}
```

- [ ] **Step 2: Run the end-to-end test to verify failure**

Run: `uv run pytest tests/test_e2e.py -v`

Expected: FAIL because `.github/agents/requirements-review.agent.md` and `.vscode/mcp.json` do not exist.

- [ ] **Step 3: Add portable VS Code MCP configuration**

Create `.vscode/mcp.json` using a workspace-relative command so no developer-specific absolute path is committed:

```json
{
  "servers": {
    "requirements-review": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "requirements-review-mcp"]
    }
  }
}
```

Document that VS Code must open the repository root and that Copilot Business/Enterprise administrators must enable MCP.

- [ ] **Step 4: Add the custom Agent workflow**

The `.agent.md` frontmatter names the Agent `Requirements Review`, grants only the six `requirements-review/*` MCP tools plus read access, and disables unrelated editing/shell tools. Its body must require this exact sequence:

```markdown
---
name: Requirements Review
description: Review text/table PDF requirements for manual and automation test gaps.
tools: ['read', 'requirements-review/*']
---

You are a test requirements reviewer. Use only workspace-local PDFs and the
requirements-review MCP tools. Write explanations and questions in Chinese,
but preserve technical terms and source quotations in their original language.
Never describe testability or suggested-scenario coverage as real test coverage.
```

1. Ask for a workspace-local PDF path, rule pack, and one of `copilot`, `company_api`, or `local`.
2. State provider and data destination and get explicit confirmation for non-Copilot external transfer.
3. Call `prepare_review`; stop on unsupported/scanned/encrypted PDFs.
4. For Copilot mode, call `get_analysis_batch`, produce only schema-valid results, and call `submit_analysis` for every batch.
5. For company/local modes, call `run_provider_analysis`.
6. Call `finalize_review`, then `get_review_status`.
7. Summarize blocking findings, both metric definitions, failed items, and local artifact paths in Chinese while preserving English technical terms.

The Agent must explicitly say both metrics are not real test-case/execution coverage.

- [ ] **Step 5: Add Windows setup and security documentation**

README commands:

```powershell
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
uv run mcp dev src/requirements_review_agent/server.py
```

Document VS Code agent discovery, MCP start/trust, environment variables, local-only `.runs/`, synthetic demo usage, common errors, no OCR, and how to inspect Chat customization diagnostics. `.env.example` contains variable names with empty values only.

- [ ] **Step 6: Run complete acceptance suite**

Run: `uv run pytest -v`

Expected: all unit, contract, fault, service, MCP, and end-to-end tests PASS without network or secrets.

Run: `uv run ruff check . && uv run mypy src`

Expected: PASS.

Run: `uv run python -c "from requirements_review_agent.server import mcp; print(type(mcp).__name__)"`

Expected: prints `MCPServer`; this command imports the server but does not start stdio transport.

- [ ] **Step 7: Perform manual Windows smoke test**

In VS Code, run `MCP: List Servers`, start `requirements-review`, select `Requirements Review`, use the synthetic PDF, and verify JSON/Markdown/DOCX open from `.runs/<run-id>/reports/`. Confirm Chat names both metrics correctly, shows every failed item, and preserves `Wi-Fi`, `BLE`, API, and state names.

- [ ] **Step 8: Commit**

```powershell
git add .github .vscode .env.example .gitignore README.md tests/test_e2e.py
git commit -m "feat: deliver portable requirements review agent"
```

---

## Final Verification

- [ ] Run `uv lock --check` and confirm the lock matches `pyproject.toml`.
- [ ] Run `uv run pytest -v` and confirm all tests pass offline.
- [ ] Run `uv run ruff check .` and `uv run mypy src` with zero findings.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Inspect a generated JSON, Markdown, and DOCX trio and confirm matching requirement IDs, scores, findings, evidence, and failures.
- [ ] Confirm `.runs/`, `.env`, and input PDFs are untracked with `git status --ignored --short`.
- [ ] Confirm no committed file contains a real endpoint credential or confidential requirement text.
