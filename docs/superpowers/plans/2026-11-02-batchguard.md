# BatchGuard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Publish a tested, documented data-quality portfolio project for the week of November 2, 2026.

**Architecture:** A deterministic CSV validator produces normalized records and row issues. A SQLite repository classifies cross-batch duplicates under a write transaction, evaluates the gate, and atomically commits immutable audit reports and accepted facts. An argparse CLI and subprocess demo expose the workflow.

**Tech Stack:** Python 3.11+, standard-library CSV/Decimal/SQLite, pytest, Ruff, Docker, GitHub Actions.

**Spec:** `docs/design.md`

## Global constraints

- Exact seven-column sales contract; 5 MiB and 50,000-record limits.
- Money uses integer minor units; threshold comparisons use Decimal.
- No mutation of accepted facts; no partial publication on gate failure.
- Original synthetic examples only; no credentials, paid services, or unrelated repository changes.

## Review focus

- Malformed quoting, BOMs, and duplicate headers must not silently change schema (parser tests).
- Duplicate IDs include invalid rows and must quarantine every occurrence (parser tests).
- Repeating a batch after other publication must preserve its original report (repository tests).
- Competing publishers must not overwrite facts or produce unaudited partial writes (repository tests).
- CLI output and exit codes distinguish blocked data from operational errors (CLI subprocess tests).

## Task 1: Contract validation (worker)

Files: `src/batchguard/contract.py`, `tests/test_contract.py`.

Interfaces: frozen `Contract(max_reject_fraction=Decimal('0.20'), max_bytes=5242880, max_rows=50000)`, `fingerprint() -> str`, `to_dict() -> dict`; `validate(data: bytes, contract: Contract) -> ValidationResult`. Result fields: `file_sha256`, `contract_fingerprint`, `rows: list[RowResult]`, `fatal_errors: list[dict]`. Row fields: `record_number`, `raw`, `record` (normalized dict or None), `errors: list[dict]`. Each error has `code`, `field`, `message`. Normalized fields: order_id, ordered_at (UTC ISO with Z), customer_id, sku, quantity, unit_price_minor, currency, total_minor.

- [x] Write failing parser/contract tests, including review-focus cases and exact money normalization.
- [x] Implement validation and fingerprinting; run owned tests and lint checks.

## Task 2: Transactional publication and CLI (primary)

Files: `src/batchguard/store.py`, `cli.py`, `tests/test_store.py`, `tests/test_cli.py`.

Interfaces: `Warehouse(path)`, `ingest(data,source,contract=None)->dict`, `report(batch_id)->dict|None`, `quarantine(batch_id)->list[dict]`, `summary()->list[dict]`. Reports include batch_id/source/raw_sha256/contract/status/reused/counts/error_fraction/fatal_errors/rows; raw input excluded from normal reports.

- [x] Write failing tests for quality gate, replay, cross-batch conflicts, rollback and concurrency.
- [x] Implement repository using explicit BEGIN IMMEDIATE and one atomic commit; prove failure rollback.
- [x] Add CLI tests for ingest/report/quarantine/summary and exit codes, then implement bounded file loading and JSON output.

## Task 3: Demonstration and publication (primary with documentation worker)

Files: synthetic `examples/*.csv`, `scripts/demo.py`, README, interview/validation notes, Dockerfile and CI.

- [x] Run synthetic workflow and assert quarantine, blocked promotion, recovery and exact currency totals.
- [x] Run suite, lint/format, package build and clean wheel/demo installation.
- [x] Receive independent review, fix material findings and verify changes.
- [ ] Publish new repository, confirm remote commit and CI; complete weekly ledger and deliver two resume drafts plus exercise.

The scheduled instructions authorize sensible defaults and full execution. This standalone repository isolates work from RelayQueue; no extra brainstorming or plan approval is required.
