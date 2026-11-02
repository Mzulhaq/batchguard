from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal, localcontext

import pytest

from batchguard.contract import Contract, validate

HEADER = "order_id,ordered_at,customer_id,sku,quantity,unit_price,currency"
GOOD = "o-1,2026-11-02T12:00:00-05:00,c-1,sku_1,2,12.34,USD"


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def test_contract_default_policy_and_semantic_fingerprint():
    default = Contract()
    padded = Contract(max_reject_fraction=Decimal("0.200"))
    assert default.max_reject_fraction == Decimal("0.20")
    assert default.fingerprint() == padded.fingerprint()
    assert default.to_dict()["version"] == "sales-v1"
    assert default.to_dict()["max_reject_fraction"] == "0.2"
    assert (
        default.fingerprint()
        == hashlib.sha256(
            json.dumps(default.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert Contract(max_reject_fraction="0.2").fingerprint() == default.fingerprint()
    assert default.to_dict()["rules"]["csv"] == {
        "dialect": "excel",
        "strict": True,
        "field_limit": 5 * 1024 * 1024,
    }


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 0.2, Decimal("NaN"), Decimal("Infinity"), "-0.01", "1.01"]
)
def test_reject_fraction_must_be_finite_exact_and_in_range(value):
    with pytest.raises((TypeError, ValueError)):
        Contract(max_reject_fraction=value)


def test_validate_normalizes_timestamp_and_money_without_float():
    result = validate(csv_bytes(GOOD), Contract())
    assert result.file_sha256 == hashlib.sha256(csv_bytes(GOOD)).hexdigest()
    assert result.contract_fingerprint == Contract().fingerprint()
    assert result.fatal_errors == []
    row = result.rows[0]
    assert row.record_number == 2
    assert row.raw == dict(zip(HEADER.split(","), GOOD.split(","), strict=True))
    assert row.errors == []
    assert row.record == {
        "order_id": "o-1",
        "ordered_at": "2026-11-02T17:00:00Z",
        "customer_id": "c-1",
        "sku": "sku_1",
        "quantity": 2,
        "unit_price_minor": 1234,
        "currency": "USD",
        "total_minor": 2468,
    }


@pytest.mark.parametrize("price,minor", [("0", 0), ("0.01", 1), ("1000000.00", 100000000)])
def test_money_boundaries_normalize_to_integer_minor_units(price, minor):
    fields = GOOD.split(",")
    fields[4] = "1"
    fields[5] = price
    result = validate(csv_bytes(",".join(fields)), Contract())
    assert result.fatal_errors == []
    assert result.rows[0].record["unit_price_minor"] == minor
    assert result.rows[0].record["total_minor"] == minor


def test_money_minor_units_ignore_callers_decimal_precision():
    fields = GOOD.split(",")
    fields[4] = "3"
    fields[5] = "12345.67"
    with localcontext() as context:
        context.prec = 3
        result = validate(csv_bytes(",".join(fields)), Contract())
    record = result.rows[0].record
    assert record is not None
    assert record["unit_price_minor"] == 1234567
    assert record["total_minor"] == 3703701


def test_long_leading_zero_price_is_exact_and_never_leaks_raw_value():
    fields = GOOD.split(",")
    fields[5] = "0" * 5000 + "123.45"
    result = validate(csv_bytes(",".join(fields)), Contract())
    assert result.rows[0].record["unit_price_minor"] == 12345
    assert result.rows[0].record["total_minor"] == 24690


def test_header_order_and_utf8_bom_are_supported():
    reversed_header = ",".join(reversed(HEADER.split(",")))
    reversed_row = ",".join(reversed(GOOD.split(",")))
    result = validate(("\ufeff" + reversed_header + "\n" + reversed_row).encode(), Contract())
    assert result.fatal_errors == []
    assert result.rows[0].record["order_id"] == "o-1"


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        ("order_id", "bad/ID", "invalid_identifier"),
        ("customer_id", "雪", "invalid_identifier"),
        ("sku", "", "invalid_identifier"),
        ("quantity", "0", "invalid_quantity"),
        ("quantity", "10001", "invalid_quantity"),
        ("quantity", "1.0", "invalid_quantity"),
        ("quantity", "１２", "invalid_quantity"),
        ("unit_price", "-0.01", "invalid_price"),
        ("unit_price", "1.001", "invalid_price"),
        ("unit_price", "1e2", "invalid_price"),
        ("unit_price", "NaN", "invalid_price"),
        ("unit_price", "1000000.01", "invalid_price"),
        ("currency", "JPY", "invalid_currency"),
        ("ordered_at", "2026-11-02T12:00:00", "invalid_timestamp"),
        ("ordered_at", "2026-02-30T12:00:00Z", "invalid_timestamp"),
    ],
)
def test_invalid_fields_return_stable_nonleaking_error(column, value, code):
    fields = GOOD.split(",")
    fields[HEADER.split(",").index(column)] = value
    result = validate(csv_bytes(",".join(fields)), Contract())
    row = result.rows[0]
    assert row.record is None
    assert code in {error["code"] for error in row.errors}
    assert all(set(error) == {"code", "field", "message"} for error in row.errors)
    if len(value) > 2:
        assert all(value not in error["message"] for error in row.errors)


def test_multiple_errors_count_as_one_invalid_row_for_quality_gate():
    rows = [
        "bad/id,2026-11-02T12:00:00Z,c-1,sku,0,1.001,USD",
        GOOD.replace("o-1", "o-2"),
        GOOD.replace("o-1", "o-3"),
        GOOD.replace("o-1", "o-4"),
        GOOD.replace("o-1", "o-5"),
    ]
    result = validate(csv_bytes(*rows), Contract(max_reject_fraction=Decimal("0.20")))
    assert len(result.rows[0].errors) >= 3
    assert sum(row.record is None for row in result.rows) == 1
    assert result.error_fraction == Decimal("0.20")
    assert (
        result.error_fraction <= Contract(max_reject_fraction=Decimal("0.20")).max_reject_fraction
    )


def test_duplicate_ids_quarantine_every_occurrence_including_invalid_row():
    invalid_duplicate = "o-1,2026-11-02T12:00:00Z,c-1,sku,0,1.00,USD"
    valid_duplicate = "o-1,2026-11-02T12:00:00Z,c-1,sku,1,1.00,USD"
    result = validate(csv_bytes(invalid_duplicate, valid_duplicate), Contract())
    assert all(row.record is None for row in result.rows)
    assert all(
        "duplicate_order_id" in {error["code"] for error in row.errors} for row in result.rows
    )


def test_wrong_field_count_preserves_extra_raw_values_and_quarantines_row():
    result = validate(csv_bytes(GOOD + ",unexpected"), Contract())
    row = result.rows[0]
    assert row.record is None
    assert row.raw["__extra__"] == ["unexpected"]
    assert row.raw["order_id"] == "o-1"
    assert "wrong_field_count" in {error["code"] for error in row.errors}


@pytest.mark.parametrize(
    "data",
    [
        b"",
        (HEADER + "\n").encode(),
        b"\xff\xfe\x00",
        (HEADER + '\n"unterminated\n').encode(),
    ],
)
def test_empty_and_malformed_files_are_fatal(data):
    result = validate(data, Contract())
    assert result.fatal_errors
    assert result.rows == []
    assert all(set(error) == {"code", "field", "message"} for error in result.fatal_errors)


def test_malformed_csv_after_good_row_retains_prior_row_and_blocks_file():
    data = (HEADER + "\n" + GOOD + '\n"broken\n').encode()
    result = validate(data, Contract())
    assert result.rows[0].record is not None
    assert result.fatal_errors[0]["code"] == "malformed_csv"


@pytest.mark.parametrize(
    "header",
    [
        "order_id,ordered_at,customer_id,sku,quantity,unit_price",
        HEADER + ",extra",
        "order_id,ordered_at,customer_id,sku,quantity,unit_price,unit_price",
    ],
)
def test_missing_extra_or_duplicate_headers_are_fatal(header):
    result = validate(csv_bytes(GOOD, header=header), Contract())
    assert result.rows == []
    assert result.fatal_errors


def test_exact_resource_limits_are_allowed_but_one_over_is_fatal():
    content = csv_bytes(GOOD)
    assert validate(content, Contract(max_bytes=len(content), max_rows=1)).fatal_errors == []
    assert validate(content, Contract(max_bytes=len(content) - 1, max_rows=1)).fatal_errors
    assert validate(content, Contract(max_bytes=len(content), max_rows=0 + 1)).fatal_errors == []
    two_rows = csv_bytes(GOOD, GOOD.replace("o-1", "o-2"))
    assert validate(two_rows, Contract(max_bytes=len(two_rows), max_rows=1)).fatal_errors
    # Caller-supplied policy cannot raise the hard contract maximums.
    assert validate(content, Contract(max_bytes=6_000_000, max_rows=60_000)).fatal_errors == []


def test_default_byte_and_row_limits_at_exact_boundary():
    max_bytes = 5 * 1024 * 1024
    base = csv_bytes(GOOD)
    exact_bytes = base + b"\n" * (max_bytes - len(base))
    assert validate(exact_bytes, Contract()).fatal_errors == []
    assert validate(exact_bytes + b"\n", Contract()).fatal_errors[0]["code"] == "file_too_large"

    header = (HEADER + "\n").encode()
    row = GOOD.replace("o-1", "order").encode() + b"\n"
    exact_rows = header + b"".join(
        GOOD.replace("o-1", f"order-{index}").encode() + b"\n" for index in range(50_000)
    )
    parsed = validate(exact_rows, Contract())
    assert parsed.fatal_errors == []
    assert len(parsed.rows) == 50_000
    over_rows = exact_rows + row.replace(b"order", b"extra")
    assert validate(over_rows, Contract()).fatal_errors[0]["code"] == "too_many_rows"
    assert validate(over_rows, Contract(max_rows=60_000)).fatal_errors[0]["code"] == "too_many_rows"
    assert (
        validate(exact_bytes + b"\n", Contract(max_bytes=max_bytes + 1)).fatal_errors[0]["code"]
        == "file_too_large"
    )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-01-01T00:30:00+00:30",
        "2026-01-01T00:30:00.125+00:30",
        "2026-01-01T00:00:00-00:00",
    ],
)
def test_valid_boundary_timestamps_are_normalized(timestamp):
    fields = GOOD.split(",")
    fields[1] = timestamp
    result = validate(csv_bytes(",".join(fields)), Contract())
    assert result.rows[0].record is not None
    assert result.rows[0].record["ordered_at"].endswith("Z")


def test_timestamp_utc_conversion_overflow_becomes_row_error():
    fields = GOOD.split(",")
    fields[1] = "0001-01-01T00:00:00+23:59"
    result = validate(csv_bytes(",".join(fields)), Contract())
    assert result.rows[0].record is None
    assert "invalid_timestamp" in {error["code"] for error in result.rows[0].errors}


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-11-02T12:00:00.1234561Z",
        "2026-11-02T12:00:00.1234569Z",
        "2026-11-02X12:00:00Z",
        "2026-11-02T12:00:00+00:00:30",
        "2026-11-02T12:00:00+00:60",
        "2026-11-02T12:00:00+01:99",
        "2026-11-02T12:00:00+24:00",
    ],
)
def test_timestamp_rejects_precision_or_shapes_that_would_be_truncated(timestamp):
    fields = GOOD.split(",")
    fields[1] = timestamp
    result = validate(csv_bytes(",".join(fields)), Contract())
    assert result.rows[0].record is None
    assert "invalid_timestamp" in {error["code"] for error in result.rows[0].errors}


def test_equivalent_timestamp_offsets_have_same_canonical_value():
    canonical = []
    for timestamp in ("2026-11-02T17:00:00Z", "2026-11-02T12:00:00-05:00"):
        fields = GOOD.split(",")
        fields[1] = timestamp
        canonical.append(validate(csv_bytes(",".join(fields)), Contract()).rows[0].record)
    assert canonical[0]["ordered_at"] == canonical[1]["ordered_at"]


def test_literal_quote_in_unquoted_field_is_a_row_identifier_error_not_fatal():
    data = (HEADER + '\no-1,2026-11-02T12:00:00Z,c"1,sku,1,1.00,USD\n').encode()
    result = validate(data, Contract())
    assert result.fatal_errors == []
    assert result.rows[0].raw["customer_id"] == 'c"1'
    assert result.rows[0].record is None
    assert "invalid_identifier" in {error["code"] for error in result.rows[0].errors}


def test_large_valid_csv_field_uses_deterministic_limit_and_restores_global():
    original = csv.field_size_limit()
    csv.field_size_limit(64)
    try:
        fields = GOOD.split(",")
        fields[5] = "0" * 140_000 + "1.00"
        result = validate(csv_bytes(",".join(fields)), Contract())
        assert result.fatal_errors == []
        assert result.rows[0].record["unit_price_minor"] == 100
        assert csv.field_size_limit() == 64
    finally:
        csv.field_size_limit(original)


def test_csv_field_limit_is_restored_after_fatal_parse():
    original = csv.field_size_limit()
    csv.field_size_limit(64)
    try:
        result = validate((HEADER + '\n"unterminated').encode(), Contract())
        assert result.fatal_errors
        assert csv.field_size_limit() == 64
    finally:
        csv.field_size_limit(original)
