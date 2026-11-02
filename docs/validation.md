# Validation evidence

Local checks performed November 2, 2026 on Windows with Python 3.11.9.

| Command | Observed result |
| --- | --- |
| `python -m pytest --cov=batchguard --cov-report=term-missing` | 83 tests passed; 82% statement coverage. CLI integration tests launch subprocesses and are not included in the parent-process coverage measurement. |
| `python -m ruff check .` | Passed. |
| `python -m ruff format --check .` | Passed. |
| `python scripts/demo.py` | Passed all assertions: 3 distinct batches, 7 published facts, 2 quarantined records, reused replay, unchanged totals after blocked input. |
| `python -m build` | Built source distribution and wheel. |
| `python -m pip install --no-deps dist/batchguard-0.1.0-py3-none-any.whl` in a fresh virtual environment | Installed successfully without runtime dependencies; the demo and installed `batchguard --help` entry point passed. |
| `python -m pip check` | No broken requirements in both development and fresh wheel environments. |

The demo ends with EUR 280, GBP 999, and USD 3,800 minor units, across seven facts. These are asserted outcomes from ten synthetic input records across three distinct files, not throughput measurements or business impact. The initial five-row file is also replayed once.

The suite covers malformed CSV/UTF-8/header cases, bounded input, duplicate identifiers, exact money, Decimal precision independence, threshold boundaries, changed policies, conflict protection, transaction rollback after an injected SQL error, concurrent ingestion, persisted replay, and CLI exit behavior. A low-precision money regression was demonstrated failing before its fix. The demo also exposed a Windows database-handle cleanup issue, fixed by explicitly closing the inspection connection.

Docker is unavailable on the local host. Container build and execution, and Linux/Python 3.13 behavior, are checked by the repository CI workflow; remote results will be recorded after publication. There are no claimed benchmarks or production deployment results.

An independent AI-assisted code review reproduced timestamp precision loss and an implicit CSV field-size limit. Both received regression tests and fixes; the review also prompted explicit documentation of Python CSV quoting semantics and CLI preflight errors. A follow-up review ran all 83 tests and the original reproductions and found no remaining material issues. This review is additional evidence, not a guarantee of defect-free software.
