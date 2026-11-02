"""Run the installed CLI against synthetic data and assert the observable outcomes."""

import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(db, *args, expected_exit=0):
    process = subprocess.run(
        [sys.executable, "-m", "batchguard.cli", "--db", str(db), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode != expected_exit:
        raise RuntimeError(f"CLI exit {process.returncode}: {process.stderr or process.stdout}")
    return json.loads(process.stdout)


def main():
    with tempfile.TemporaryDirectory(prefix="batchguard-demo-") as temporary:
        db = Path(temporary) / "warehouse.db"
        initial = run(db, "ingest", ROOT / "examples/initial.csv", "--source", "demo-shop")
        assert initial["counts"] == {"accepted": 4, "duplicate": 0, "quarantined": 1, "withheld": 0}
        replay = run(db, "ingest", ROOT / "examples/initial.csv", "--source", "demo-shop")
        assert replay["reused"] and replay["batch_id"] == initial["batch_id"]
        rejected = run(db, "quarantine", initial["batch_id"])
        assert len(rejected) == 1 and rejected[0]["raw"]["order_id"] == "ord-005"
        before = run(db, "summary")
        blocked = run(
            db, "ingest", ROOT / "examples/blocked.csv", "--source", "demo-shop", expected_exit=2
        )
        assert blocked["counts"]["withheld"] == 1
        assert blocked["counts"]["quarantined"] == 1
        assert run(db, "summary") == before
        corrected = run(db, "ingest", ROOT / "examples/corrected.csv", "--source", "demo-shop")
        assert corrected["counts"]["accepted"] == 3
        assert run(db, "report", blocked["batch_id"]) == blocked
        totals = run(db, "summary")
        assert totals == [
            {"currency": "EUR", "orders": 2, "quantity": 5, "total_minor": 280},
            {"currency": "GBP", "orders": 1, "quantity": 1, "total_minor": 999},
            {"currency": "USD", "orders": 4, "quantity": 6, "total_minor": 3800},
        ]
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 3
            assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 7
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        print(
            json.dumps(
                {
                    "result": "PASS",
                    "distinct_batches": 3,
                    "published_facts": 7,
                    "replay_reused": True,
                    "blocked_batch_changed_totals": False,
                    "quarantined_records": 2,
                    "totals": totals,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
