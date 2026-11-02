import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from batchguard.contract import Contract
from batchguard.store import Warehouse
from tests.helpers import sale, sales_csv


@pytest.fixture
def warehouse(tmp_path):
    return Warehouse(tmp_path / "warehouse.db")


def test_publishes_exact_money_and_fact_lineage(warehouse):
    report = warehouse.ingest(sales_csv(sale(unit_price="0.10", quantity="3")), "shop")
    assert report["status"] == "published"
    assert report["counts"] == {"accepted": 1, "duplicate": 0, "quarantined": 0, "withheld": 0}
    assert warehouse.summary() == [
        {"currency": "USD", "orders": 1, "quantity": 3, "total_minor": 30}
    ]
    with sqlite3.connect(warehouse.path) as conn:
        assert conn.execute("SELECT batch_id, record_number FROM facts").fetchone() == (
            report["batch_id"],
            2,
        )


def test_gate_equality_passes_and_quarantines_one_record(warehouse):
    data = sales_csv(*(sale(f"o-{n}", quantity="0" if n == 4 else "1") for n in range(5)))
    report = warehouse.ingest(data, "shop")
    assert report["status"] == "published"
    assert report["counts"]["accepted"] == 4
    assert report["counts"]["quarantined"] == 1
    assert Decimal(report["error_fraction"]) == Decimal("0.2")
    quarantine = warehouse.quarantine(report["batch_id"])
    assert len(quarantine) == 1
    assert quarantine[0]["raw"]["quantity"] == "0"
    assert all("raw" not in row for row in report["rows"])


def test_failing_gate_withholds_every_valid_row(warehouse):
    report = warehouse.ingest(sales_csv(sale("ok"), sale("bad", quantity="0")), "shop")
    assert report["status"] == "blocked"
    assert report["counts"] == {"accepted": 0, "duplicate": 0, "quarantined": 1, "withheld": 1}
    assert warehouse.summary() == []
    assert warehouse.report(report["batch_id"])["status"] == "blocked"


def test_fatal_file_is_audited_but_never_published(warehouse):
    report = warehouse.ingest(b"unexpected,header\n1,2\n", "shop")
    assert report["status"] == "blocked"
    assert report["fatal_errors"]
    assert warehouse.report(report["batch_id"])["fatal_errors"]
    assert warehouse.summary() == []


def test_replay_returns_immutable_report_after_other_runs(warehouse):
    data = sales_csv(sale())
    original = warehouse.ingest(data, "shop")
    warehouse.ingest(sales_csv(sale("o-2")), "shop")
    replay = Warehouse(warehouse.path).ingest(data, "shop")
    assert replay["batch_id"] == original["batch_id"]
    assert replay["reused"] is True
    assert {k: v for k, v in replay.items() if k != "reused"} == {
        k: v for k, v in original.items() if k != "reused"
    }
    assert warehouse.summary()[0]["orders"] == 2


def test_cross_batch_normalized_duplicate_does_not_double_count(warehouse):
    warehouse.ingest(sales_csv(sale(unit_price="12.5")), "shop")
    report = warehouse.ingest(sales_csv(sale(unit_price="12.50")), "shop")
    assert report["status"] == "published"
    assert report["counts"]["duplicate"] == 1
    assert report["counts"]["accepted"] == 0
    assert warehouse.summary()[0]["orders"] == 1


def test_conflicting_record_never_overwrites_existing_fact(warehouse):
    warehouse.ingest(sales_csv(sale()), "shop")
    report = warehouse.ingest(sales_csv(sale(unit_price="50")), "shop")
    assert report["status"] == "blocked"
    assert report["rows"][0]["errors"][0]["code"] == "existing_record_conflict"
    assert warehouse.summary()[0]["total_minor"] == 2500


def test_sources_are_independent_and_currencies_never_combined(warehouse):
    warehouse.ingest(sales_csv(sale()), "first")
    warehouse.ingest(sales_csv(sale(currency="EUR")), "second")
    assert warehouse.summary() == [
        {"currency": "EUR", "orders": 1, "quantity": 2, "total_minor": 2500},
        {"currency": "USD", "orders": 1, "quantity": 2, "total_minor": 2500},
    ]


def test_publication_failure_rolls_back_audit_and_facts(warehouse):
    with sqlite3.connect(warehouse.path) as conn:
        conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON facts
                     WHEN NEW.order_id='o-2' BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        warehouse.ingest(sales_csv(sale(), sale("o-2")), "shop")
    assert warehouse.summary() == []
    with sqlite3.connect(warehouse.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM row_audit").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_second")
    assert warehouse.ingest(sales_csv(sale(), sale("o-2")), "shop")["counts"]["accepted"] == 2


def test_concurrent_same_batch_publishes_once(warehouse):
    barrier = threading.Barrier(4)

    def ingest(_):
        barrier.wait(timeout=10)
        return warehouse.ingest(sales_csv(sale()), "shop")

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(ingest, range(4)))
    assert sum(not report["reused"] for report in reports) == 1
    assert len({report["batch_id"] for report in reports}) == 1
    assert warehouse.summary()[0]["orders"] == 1


def test_concurrent_conflicting_batches_keep_first_committed_fact(warehouse):
    barrier = threading.Barrier(2)

    def ingest(price):
        barrier.wait(timeout=10)
        return warehouse.ingest(sales_csv(sale(unit_price=price)), "shop")

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(ingest, ["1.00", "2.00"]))
    assert sorted(report["status"] for report in reports) == ["blocked", "published"]
    assert warehouse.summary()[0]["orders"] == 1
    assert warehouse.summary()[0]["total_minor"] in {200, 400}


@pytest.mark.parametrize("source", ["", "bad/source", "x" * 65, "é"])
def test_invalid_source_rejected(warehouse, source):
    with pytest.raises(ValueError, match="source"):
        warehouse.ingest(sales_csv(sale()), source)


def test_unknown_batch_is_not_a_report(warehouse):
    assert warehouse.report("missing") is None
    with pytest.raises(KeyError):
        warehouse.quarantine("missing")


def test_gate_uses_exact_ratio_not_rounded_display_fraction(warehouse):
    policy = Contract(max_reject_fraction=Decimal("0.3333333333333333333333333333"))
    data = sales_csv(sale("one"), sale("two"), sale("bad", quantity="0"))
    report = warehouse.ingest(data, "shop", policy)
    assert report["status"] == "blocked"  # One third is strictly larger than the policy.
    assert report["counts"]["withheld"] == 2
    assert warehouse.summary() == []


def test_gate_counts_bad_records_once_not_number_of_issues(warehouse):
    data = sales_csv(sale("bad", quantity="0", currency="INVALID"), sale("ok"))
    report = warehouse.ingest(data, "shop", Contract(max_reject_fraction="0.5"))
    assert report["status"] == "published"
    assert len(report["rows"][0]["errors"]) == 2
    assert report["counts"]["quarantined"] == 1


def test_new_policy_is_a_new_batch_but_original_blocked_report_is_immutable(warehouse):
    data = sales_csv(sale("ok"), sale("bad", quantity="0"))
    original = warehouse.ingest(data, "shop")
    relaxed = warehouse.ingest(data, "shop", Contract(max_reject_fraction="0.5"))
    assert relaxed["batch_id"] != original["batch_id"]
    assert relaxed["counts"]["accepted"] == 1
    assert warehouse.report(original["batch_id"]) == original
    replay = warehouse.ingest(data, "shop")
    assert replay["status"] == "blocked"
    assert replay["reused"]
    assert warehouse.summary()[0]["orders"] == 1
