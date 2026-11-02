import json
import subprocess
import sys

from tests.helpers import sale, sales_csv


def run_cli(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "batchguard.cli", "--db", str(db), *args],
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_cli_ingest_report_summary_and_replay(tmp_path):
    db = tmp_path / "demo.db"
    file = tmp_path / "sales.csv"
    file.write_bytes(sales_csv(sale()))
    ingested = run_cli(db, "ingest", str(file), "--source", "shop")
    assert ingested.returncode == 0
    assert ingested.stdout.strip(), "ingest must print a machine-readable report"
    report = json.loads(ingested.stdout)
    assert report["counts"]["accepted"] == 1
    assert json.loads(run_cli(db, "report", report["batch_id"]).stdout) == report
    assert json.loads(run_cli(db, "ingest", str(file), "--source", "shop").stdout)["reused"]
    assert json.loads(run_cli(db, "summary").stdout)[0]["total_minor"] == 2500


def test_blocked_data_returns_two_and_explicit_quarantine(tmp_path):
    db = tmp_path / "demo.db"
    file = tmp_path / "bad.csv"
    file.write_bytes(sales_csv(sale(quantity="0")))
    result = run_cli(db, "ingest", str(file), "--source", "shop")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    rejected = run_cli(db, "quarantine", report["batch_id"])
    assert rejected.returncode == 0
    assert json.loads(rejected.stdout)[0]["raw"]["quantity"] == "0"


def test_missing_file_and_report_are_operational_errors(tmp_path):
    db = tmp_path / "demo.db"
    for args in (
        ("ingest", str(tmp_path / "missing.csv"), "--source", "shop"),
        ("report", "missing"),
        ("quarantine", "missing"),
    ):
        result = run_cli(db, *args)
        assert result.returncode == 1
        assert json.loads(result.stderr)["error"]
        assert "Traceback" not in result.stderr


def test_cli_can_set_quality_policy(tmp_path):
    file = tmp_path / "bad.csv"
    file.write_bytes(sales_csv(sale("ok"), sale("bad", quantity="0")))
    result = run_cli(
        tmp_path / "demo.db",
        "ingest",
        str(file),
        "--source",
        "shop",
        "--max-reject-fraction",
        "0.50",
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["counts"]["accepted"] == 1


def test_invalid_policy_does_not_create_database(tmp_path):
    db = tmp_path / "invalid.db"
    file = tmp_path / "sales.csv"
    file.write_bytes(sales_csv(sale()))
    result = run_cli(db, "ingest", str(file), "--source", "shop", "--max-reject-fraction", "NaN")
    assert result.returncode == 1
    assert not db.exists()


def test_oversized_file_rejected_before_database_creation(tmp_path):
    file = tmp_path / "oversize.csv"
    file.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    db = tmp_path / "unused.db"
    result = run_cli(db, "ingest", str(file), "--source", "shop")
    assert result.returncode == 1
    assert not db.exists()
