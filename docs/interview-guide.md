# BatchGuard interview guide

These notes describe the implementation in this repository. Explain the tradeoffs and limits, and be ready to trace the relevant code rather than treating the project as a production platform.

## Discussion questions

### Why use a data contract before inserting rows?

It makes the accepted shape and rules explicit: seven named columns, bounded input, exact numeric syntax, allowed currencies, and timestamps with offsets. Stable error codes let operators find the type of problem without echoing raw values into messages. The exact header set can arrive in a different order, but missing, extra, or duplicated header names are fatal.

CSV uses Python's strict Excel dialect. Parser errors block the file; a quote within an unquoted field is a literal character and goes through row validation. Timestamps use a documented subset with mandatory seconds and at most six fractional digits, preventing silent precision loss during normalization.

### Why does exactly 20% rejected pass?

The default threshold is a `Decimal` value. The gate compares exact integer ratios rather than floating-point approximations and uses `<=`, so equality passes. It counts rejected records, even when one record has multiple validation errors. Fatal file-level errors block the batch regardless of the row fraction.

### What is the difference between a rejected row and a blocked batch?

A rejected row is quarantined. If the rejected fraction is within policy, otherwise-valid rows can be published. If the fraction is over the limit or there is a fatal file error, the batch is blocked and all otherwise-valid new rows are marked withheld. Nothing from a blocked batch is added to the facts table.

### How is publication kept atomic?

Validation and parsing happen before opening the write transaction. The store then begins an immediate SQLite transaction, checks replay and cross-batch conflicts, and writes the batch record, row audit, and accepted facts together. If any insert fails, all changes roll back. The write lock serializes competing publication decisions, including two batches that contain conflicting versions of the same source and order ID.

### What makes replay safe?

The batch ID hashes the source, raw-file SHA-256, and complete contract fingerprint, including the quality threshold and limits. Reingesting the same identity returns the stored report. A different threshold creates a different batch identity. The report's `reused` flag describes the current call; the persisted report remains immutable.

### How are duplicate and conflicting facts handled?

Facts are keyed by `(source, order_id)`. A cross-batch row with the same normalized-content hash is reported as a duplicate and does not double count. Changed normalized content is quarantined as an existing-record conflict. Existing facts are append-only and are not overwritten. Within a single file, every occurrence of a repeated order ID is quarantined, including otherwise identical occurrences.

### Why store money as integer minor units?

The parser accepts a bounded plain-decimal syntax and converts its whole and fractional digits directly to integer minor units. It avoids binary floating-point rounding and dependence on a caller's Decimal precision. Quantity times unit price yields the exact minor-unit total. Summaries stay grouped by currency, so USD, EUR, and GBP amounts are never added together.

### Which data should operators treat as sensitive?

The audit database stores the raw fields from every row, including accepted records. Ordinary reports omit raw row values; the explicit `quarantine` command reveals raw values for rejected rows. Stable error messages do not copy field contents. The local database is not encrypted, so the operator must protect it and decide retention and deletion practices.

## 30-minute exercise: add a batch rejection summary

Add a report field that groups quarantined rows by stable error code, while keeping the existing report free of raw input values.

Suggested steps:

1. Add a test with one row that has multiple validation errors and another row with one error.
2. Decide and document whether the summary counts issues or distinct affected rows per error code. A useful default is issue counts, with the batch quality gate still counting rejected rows.
3. Build the summary from the stored row decisions inside the same transaction that creates the immutable report.
4. Add a replay assertion proving that a stored report returns the same summary.
5. Confirm that raw values do not appear in the new report field.

Hint: row decision errors already carry stable `code`, `field`, and `message` fields. The immutable report records those errors even when later operator queries are made. Avoid recalculating a past report from current facts, since facts and subsequent batches are separate from that batch's decision history.

## Resume drafts

These drafts describe repository features, not measured impact. Adapt them to the work you personally did and keep them only if you can explain and demonstrate the behavior.

- Built a Python CSV ingestion CLI with explicit schema validation, row-level quarantine, and an exact-decimal data-quality gate.
- Implemented atomic SQLite publication with immutable batch replay, cross-batch deduplication, conflict detection, and integer minor-unit reporting.

AI assistance contributed to this project. Be candid about that assistance and accurately describe which design, implementation, testing, and review work you personally performed. Practice making a small change before using these bullets.
