# BatchGuard design

BatchGuard is a local Python/SQL data ingestion pipeline. It accepts a bounded CSV sales file, validates an explicit contract, classifies cross-batch duplicates and conflicts, applies a quality gate, and commits the audit plus accepted facts atomically. It complements a webhook service by demonstrating data contracts, ETL, row-level diagnostics, SQL analytics, lineage, and reproducibility. It has no runtime dependencies outside Python 3.11's standard library and SQLite.

## Sales contract v1

The exact header set is `order_id,ordered_at,customer_id,sku,quantity,unit_price,currency`; order may vary. UTF-8 with an optional BOM, strict CSV quoting, no duplicate or extra header names. Limits: 5 MiB input, 50,000 data records. Blank lines are ignored. Each nonblank data record has exactly seven fields. Identifiers are 1-64 ASCII letters/digits/dot/underscore/dash. Quantities are decimal digits representing 1-10000. Prices are nonnegative, plain decimal strings with at most two fractional digits, maximum 1000000.00; convert to integer minor units without binary floats. Currencies are USD, EUR, GBP (all two decimal places). Timestamps are ISO-8601 with an explicit offset and normalize to UTC. Every occurrence of an order ID repeated in the same input is quarantined, even if the repeated rows are identical. Validation messages have stable codes and never embed raw input.

CSV means Python's Excel dialect with `strict=True`, rather than a separate RFC syntax checker. Unterminated quoted fields are parser errors; quotes inside unquoted fields are literal characters that face row validation. Timestamps are restricted to `YYYY-MM-DDTHH:MM:SS[.fraction](Z|+/-HH:MM)` with one to six fractional digits. Unsupported precision is rejected to avoid losing timestamp distinctions. These policies are part of the contract fingerprint.

The default maximum rejected fraction is Decimal('0.20'), configurable from 0 to 1 inclusive. Equality passes. No rows, invalid UTF-8, CSV parser errors, wrong headers, or exceeded validator resource limits are fatal and block the whole batch. An invalid record can have multiple rule failures; the gate counts records, not issues. Every raw row in the audit is operator-local data, never published to GitHub.

## Publication and identity

Batch identity is SHA-256 of source + raw-file SHA-256 + full contract fingerprint, including the threshold and limits. Source is a 1-64-character identifier. Exact repeats return the immutable stored report. A new contract policy produces a different run. Existing facts are keyed by source/order_id: matching normalized content is a harmless duplicate; changed content is quarantined as an existing-record conflict. Facts are append-only. An accepted batch inserts new valid rows; a blocked batch withholds otherwise-valid rows and publishes none. Reports distinguish accepted, duplicate, quarantined, and withheld records.

SQLite BEGIN IMMEDIATE serializes publication decisions, including cross-batch conflict checks. Batch reports, all row decisions, and newly accepted facts share one transaction. Failures roll back all of them, so reruns after an interrupted commit are safe. Parsing happens before acquiring the write lock. Store fact lineage to batch ID and logical CSV record number (header is record 1). Raw input SHA and canonical normalized-record SHA are separate. Local audit data is not encrypted. Retention, schema migrations, distributed execution, additional currencies, schema inference and monetary FX conversion are out of scope.

## Operator interface

`batchguard --db PATH ingest CSV --source NAME [--max-reject-fraction 0.20]` prints JSON and exits 0 for published, 2 for quality-blocked, 1 for an operational error. `report BATCH_ID` shows immutable row decisions without raw row values. `quarantine BATCH_ID` explicitly reveals raw rejected rows as JSON. `summary` groups accepted record counts, quantities and exact minor-unit totals by currency. There is no web server or network credential requirement.

Before constructing the warehouse, CLI preflight rejects unreadable files, invalid policies, and files over 5 MiB with exit 1 and no audit record or database creation. Remaining validator failures are audited. Logical record numbers exclude blank records and need not equal physical line numbers in multiline input.

## Verification and delivery

Tests cover parser failures, precision, gate boundaries, immutable replays, cross-batch conflicts, rollback, concurrent publishers and CLI exit codes. A synthetic executable demo proves clean publication, quarantine, whole-batch blocking, corrected-file recovery, and duplicate reruns with asserted SQL totals. Ship exact dev dependency pins, a non-root container running the demo, multi-platform Python CI, README, measured validation, interview notes and an exercise. Publish only this project to a new public Mzulhaq/batchguard repository after local checks and review; verify remote CI before marking the weekly ledger complete.
