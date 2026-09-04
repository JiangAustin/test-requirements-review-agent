# Fast Requirements Review Design

## Goal

Make a full PDF requirements review finish from one Copilot Chat request with a small,
bounded number of model calls. Preserve local evidence validation, deterministic scoring,
resume support, and JSON/Markdown/DOCX reports.

For the Ventilation PUMU reference PDF, the acceptance target is:

- no more than 15 Copilot model batches under normal model context limits;
- no model-generated repetition of source objects, rule metadata, or report fields;
- automatic resume after a malformed response, cancellation, or quota interruption;
- every scored verdict traceable to one or more extracted PDF sources;
- completion from one user request unless VS Code requires consent or quota recovery.

## Problem

The current pipeline treats extracted blocks and table rows as independent requirements. The
reference PDF produces 1,261 candidates and 3,103 rule checks. Each Copilot submission repeats
the complete source object, rule impact, Chinese question, rationale, and scenario objects. A
50-item batch exceeds practical tool payload limits; reducing the batch to 10 creates 127
batches. The orchestration then consumes more tokens and tool calls than the actual review.

A Skill cannot remove this cost. It can describe the loop, but the Chat agent would still have
to execute every batch and generate the same verbose payload.

## Product Modes

### Fast mode (default)

Fast mode is the normal end-to-end review. It groups extracted content into logical requirement
units, runs deterministic checks locally, asks Copilot only for semantic verdicts, and accepts a
compact response contract.

### Strict mode (compatibility)

Strict mode preserves the existing atomic requirement and full `AnalysisSubmission` workflow.
It remains available for diagnostics, compatibility, and small documents, but is not the default
for interactive Copilot review.

The report records the selected mode. Fast and strict results must never be silently mixed in one
run.

## Architecture

```mermaid
flowchart LR
    PDF[PDF] --> Extract[Extract blocks and tables]
    Extract --> Group[Group logical requirements]
    Group --> Rules[Local structural checks and rule selection]
    Rules --> Batch[Token-budgeted compact batches]
    Batch --> Copilot[Copilot semantic verdicts]
    Copilot --> Expand[Validate and expand verdicts]
    Expand --> Store[Atomic checkpoint]
    Store --> Score[Deterministic scoring]
    Score --> Report[JSON / Markdown / DOCX]
```

Python remains the source of truth for extraction, grouping, rule applicability, expansion,
validation, persistence, scoring, and reporting. Copilot supplies only semantic judgments that
cannot be derived reliably by deterministic code. No VS Code extension or external model service
is required for the first implementation.

The existing MCP server remains the integration boundary. The custom Agent or an optional Skill
drives a small sequence of MCP calls; neither owns review state.

## Logical Requirement Grouping

Introduce `LogicalRequirement`, containing a stable ID, title, combined text, ordered source
references, and optional external work-item ID.

Grouping follows deterministic rules:

1. A heading matching `PREFIX-number - title`, such as `VESW-3217 - ECU state "initial"`, opens a
   work-item group.
2. Subsequent paragraphs and table rows associated with that heading join the group until the next
   work-item heading or a document section boundary that clearly ends the item.
3. Split PDF blocks belonging to one sentence remain in source order and are joined without losing
   their individual source references.
4. Standalone normative paragraphs outside a work item remain independent logical requirements.
5. Examples, notes, release history, stakeholder data, navigation text, and orphan fragments are
   retained as contextual sources or filtered; they are not independently scored.
6. Ambiguous grouping is retained with `needs_manual_review=true`; content is never silently
   assigned across unrelated work items.

The stable ID uses the external work-item ID when present and a hash of ordered source identities
otherwise. The original extracted data remains stored for audit.

## Local Structural Analysis

Before invoking Copilot, Python records deterministic features such as:

- normative modal language;
- trigger or precondition phrases;
- measurable values and units;
- explicit expected behavior;
- timeout, retry, state, diagnostic, interface, and variant terms;
- unresolved cross-reference and fragment indicators.

These features may determine only checks with an objective local rule. They otherwise become
compact hints for Copilot. Local heuristics must not claim semantic completeness solely because a
keyword exists.

## Compact Model Contract

The model input contains rule definitions once per batch and requirements as compact records:

```json
{
  "rules": {
    "behavior.acceptance": "请确认触发条件、行为和判据是否明确。"
  },
  "requirements": [
    {
      "id": "VESW-3217",
      "text": "...",
      "source_count": 3,
      "rule_ids": ["behavior.acceptance", "state.mode_transition"],
      "hints": ["modal", "state", "reset"]
    }
  ]
}
```

The model returns every requested rule verdict, but only compact dynamic fields:

```json
{
  "items": [
    {
      "id": "VESW-3217",
      "verdicts": {
        "behavior.acceptance": {
          "status": "complete",
          "evidence": [0],
          "confidence": 0.91
        },
        "state.mode_transition": {
          "status": "needs_confirmation",
          "evidence": [0, 1],
          "reason": "非法状态转换未定义"
        }
      }
    }
  ]
}
```

Evidence values are indexes into the requirement's immutable source list. Python validates indexes
and expands verdicts into the existing `CheckResult`, `ScenarioResult`, and report models. Rule
impact, severity policy, Chinese question, source geometry, finding type, and standard rationale
are never generated repeatedly by the model.

Every requirement and applicable rule must still receive an explicit status. Missing verdicts are
rejected rather than assumed complete.

## Scenario And Scoring Semantics

Scoring remains deterministic. A rule marked `complete` contributes its configured weight;
`missing` and `needs_confirmation` do not. `not_applicable` is excluded from the denominator.

Fast mode derives scenario results from applicable rule categories:

- a category is covered when at least one applicable check in that category is complete and cites
  evidence;
- a category is uncovered when all applicable checks are missing or need confirmation;
- mixed categories retain the individual check details in the report.

Reports continue to state that these metrics measure requirement testability and suggested
scenario coverage, not existing test-case coverage or execution coverage.

## Token-Budgeted Batching

The MCP server does not receive the selected Copilot model's `maxInputTokens`. The first
implementation therefore uses a configurable conservative serialized-byte ceiling, covered by
boundary tests. It budgets prompt size plus estimated compact response capacity, rather than a
fixed requirement count. A future integration that can read model metadata may provide a lower
runtime ceiling, but correctness cannot depend on that integration.

Rules are emitted once per batch. Requirements are packed until the budget threshold is reached.
One oversized requirement receives its own batch and a warning. The reference PDF must produce no
more than 15 batches in the acceptance test; if it exceeds the target, preparation returns batch
size diagnostics instead of silently creating another 100-call run.

## Orchestration And Resume

Fast mode exposes this MCP flow:

1. `prepare_review(..., review_mode="fast")`
2. `get_next_review_batch(run_id)`
3. `submit_review_verdicts(run_id, batch_id, verdicts)`
4. Repeat steps 2-3 until `done=true`
5. `finalize_review(run_id)`

`get_next_review_batch` always returns the first incomplete batch, so the Agent does not manage
indexes. Submission is idempotent by `batch_id` and content hash. Each successful submission is
persisted atomically before status is updated.

Malformed model output triggers one repair request containing only validation errors and the
invalid compact response. If repair fails, the run remains resumable and reports the exact batch;
it does not fabricate blanket `needs_confirmation` results. Cancellation and quota errors preserve
all completed batches. Repeating the original user request resumes the latest compatible run for
the same PDF hash, rule pack, mode, and schema version.

## Agent And Skill

The custom Agent becomes a thin orchestrator: prepare or resume, process each compact batch,
finalize, and summarize. It must not generate the verbose report schema itself.

An optional workspace Skill may package the same workflow for discovery from a general Copilot
agent. It is not required for correctness and does not duplicate domain rules or persistence
logic.

## Compatibility And Migration

- Existing strict runs and report files remain readable.
- Existing `get_analysis_batch` and `submit_analysis` tools remain available for strict mode.
- New fast runs use a new compact verdict schema version and separate submission filenames.
- Existing partial 127-batch runs are not migrated because their requirement granularity differs;
  they remain inspectable and may be deleted manually.
- `automotive-ecu-v1` remains explicitly selectable; rule-pack behavior is independent of review
  mode.

## Error Handling

Public errors distinguish unsupported PDF, grouping ambiguity threshold, oversized logical
requirement, compact schema failure, unknown evidence index, model quota/rate limit, and report
generation failure. Errors contain IDs and counts but never full requirement text or credentials.

No failed batch advances the checkpoint. Report generation remains retryable from a fully analyzed
run.

## Testing

Tests cover:

- deterministic work-item grouping, source order, standalone requirements, and ambiguous cases;
- compact contract validation, exact ID coverage, evidence indexes, and expansion;
- token-budget batching and the 15-batch reference-PDF acceptance target;
- idempotent submission, atomic checkpointing, malformed-response repair state, and resume;
- fast-mode scoring and cross-format report consistency;
- strict-mode backward compatibility;
- MCP end-to-end flow from synthetic PDF through finalized artifacts.

Language-model responses are represented by deterministic fixtures. Automated tests do not consume
Copilot quota. A manual smoke test uses the reference PDF and records requirement count, batch
count, request failures, elapsed time, and approximate serialized input/output size.

## Non-Goals

- Building a VS Code extension in the first implementation.
- Calling an unofficial Copilot endpoint or requiring an OpenAI-compatible local service.
- Claiming existing test coverage from requirements text.
- Automatically resolving missing external cross-references.
- Migrating incomplete strict runs into fast mode.
