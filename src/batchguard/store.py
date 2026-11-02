"""Atomic, append-only publication with immutable batch and row audit records."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from batchguard.contract import Contract, validate

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    contract_fingerprint TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS row_audit (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    record_number INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted','duplicate','quarantined','withheld')),
    raw_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    PRIMARY KEY(batch_id, record_number)
);
CREATE TABLE IF NOT EXISTS facts (
    source TEXT NOT NULL,
    order_id TEXT NOT NULL,
    ordered_at TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price_minor INTEGER NOT NULL CHECK(unit_price_minor >= 0),
    currency TEXT NOT NULL CHECK(currency IN ('USD','EUR','GBP')),
    total_minor INTEGER NOT NULL CHECK(total_minor >= 0),
    record_sha256 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    record_number INTEGER NOT NULL,
    PRIMARY KEY(source, order_id),
    FOREIGN KEY(batch_id, record_number) REFERENCES row_audit(batch_id, record_number)
);
CREATE INDEX IF NOT EXISTS facts_currency_date ON facts(currency, ordered_at);
"""


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


class Warehouse:
    """Each operation opens its own connection; a file is required for persistence."""

    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            raise ValueError("Warehouse requires an on-disk database path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError("Unsupported BatchGuard database schema version")
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA user_version=1")

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def ingest(self, data: bytes, source: str, contract: Contract | None = None) -> dict:
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", source):
            raise ValueError(
                "source must be 1-64 ASCII letters, digits, dots, dashes or underscores"
            )
        contract = contract or Contract()
        validation = validate(data, contract)
        identity = f"{source}\0{validation.file_sha256}\0{validation.contract_fingerprint}"
        batch_id = hashlib.sha256(identity.encode()).hexdigest()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = conn.execute(
                    "SELECT report_json FROM batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
                if previous is not None:
                    conn.rollback()
                    report = json.loads(previous[0])
                    report["reused"] = True
                    return report

                decisions = []
                for row in validation.rows:
                    errors = list(row.errors)
                    record = row.record
                    digest = None
                    status = "quarantined" if errors or record is None else "accepted"
                    if status == "accepted":
                        digest = hashlib.sha256(canonical_json(record).encode()).hexdigest()
                        existing = conn.execute(
                            "SELECT record_sha256 FROM facts WHERE source=? AND order_id=?",
                            (source, record["order_id"]),
                        ).fetchone()
                        if existing is not None:
                            if existing[0] == digest:
                                status = "duplicate"
                            else:
                                status = "quarantined"
                                errors.append(
                                    {
                                        "code": "existing_record_conflict",
                                        "field": "order_id",
                                        "message": "Order ID has different published content",
                                    }
                                )
                    decisions.append(
                        {
                            "row": row,
                            "record": record,
                            "digest": digest,
                            "status": status,
                            "errors": errors,
                        }
                    )

                rejected = sum(item["status"] == "quarantined" for item in decisions)
                total = len(decisions)
                numerator, denominator = contract.max_reject_fraction.as_integer_ratio()
                passed = not validation.fatal_errors and rejected * denominator <= total * numerator
                if not passed:
                    for item in decisions:
                        if item["status"] == "accepted":
                            item["status"] = "withheld"
                counts = {
                    state: sum(item["status"] == state for item in decisions)
                    for state in ("accepted", "duplicate", "quarantined", "withheld")
                }
                report = {
                    "batch_id": batch_id,
                    "source": source,
                    "raw_sha256": validation.file_sha256,
                    "contract_fingerprint": validation.contract_fingerprint,
                    "contract": contract.to_dict(),
                    "status": "published" if passed else "blocked",
                    "reused": False,
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "total_rows": total,
                    "counts": counts,
                    "error_fraction": str(Decimal(rejected) / total) if total else "0",
                    "fatal_errors": validation.fatal_errors,
                    "rows": [
                        {
                            "record_number": item["row"].record_number,
                            "status": item["status"],
                            "errors": item["errors"],
                        }
                        for item in decisions
                    ],
                }
                conn.execute(
                    "INSERT INTO batches VALUES(?,?,?,?,?)",
                    (
                        batch_id,
                        source,
                        validation.file_sha256,
                        validation.contract_fingerprint,
                        canonical_json(report),
                    ),
                )
                for item in decisions:
                    row = item["row"]
                    conn.execute(
                        "INSERT INTO row_audit VALUES(?,?,?,?,?)",
                        (
                            batch_id,
                            row.record_number,
                            item["status"],
                            canonical_json(row.raw),
                            canonical_json(item["errors"]),
                        ),
                    )
                    if item["status"] == "accepted":
                        record = item["record"]
                        conn.execute(
                            """INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                source,
                                record["order_id"],
                                record["ordered_at"],
                                record["customer_id"],
                                record["sku"],
                                record["quantity"],
                                record["unit_price_minor"],
                                record["currency"],
                                record["total_minor"],
                                item["digest"],
                                batch_id,
                                row.record_number,
                            ),
                        )
                conn.commit()
                return report
            except BaseException:
                conn.rollback()
                raise

    def report(self, batch_id: str) -> dict | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT report_json FROM batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def quarantine(self, batch_id: str) -> list[dict]:
        if self.report(batch_id) is None:
            raise KeyError("Batch not found")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM row_audit WHERE batch_id=? AND status='quarantined' "
                "ORDER BY record_number",
                (batch_id,),
            ).fetchall()
        return [
            {
                "record_number": row["record_number"],
                "raw": json.loads(row["raw_json"]),
                "errors": json.loads(row["errors_json"]),
            }
            for row in rows
        ]

    def summary(self) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT currency, COUNT(*) AS orders, SUM(quantity) AS quantity,
                SUM(total_minor) AS total_minor FROM facts GROUP BY currency ORDER BY currency"""
            ).fetchall()
        return [dict(row) for row in rows]
