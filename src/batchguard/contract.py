"""Deterministic validation for BatchGuard's versioned sales CSV contract."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

_HEADER = ("order_id", "ordered_at", "customer_id", "sku", "quantity", "unit_price", "currency")
_HARD_MAX_BYTES = 5 * 1024 * 1024
_HARD_MAX_ROWS = 50_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,64}\Z", re.ASCII)
_DIGITS = re.compile(r"[0-9]+\Z", re.ASCII)
_PRICE = re.compile(r"(?:[0-9]+)(?:\.[0-9]{1,2})?\Z", re.ASCII)
_THRESHOLD = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z",
    re.ASCII,
)
_CSV_FIELD_SIZE_LOCK = threading.RLock()


@contextmanager
def _bounded_csv_field_size() -> Iterator[None]:
    """Set the stdlib's process-global CSV field limit under our validation lock.

    External libraries calling csv.field_size_limit() do not share this lock, so
    they may observe this temporary setting while validation is parsing.
    """
    with _CSV_FIELD_SIZE_LOCK:
        previous = csv.field_size_limit()
        csv.field_size_limit(_HARD_MAX_BYTES)
        try:
            yield
        finally:
            csv.field_size_limit(previous)


def _decimal_text(value: Decimal) -> str:
    """Render a finite Decimal canonically without exponent or insignificant zeros."""
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    if not any(digits):
        return "0"
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    point = len(digits) + exponent
    prefix = "-" if sign else ""
    if point > 0:
        if point >= len(digits):
            rendered = coefficient + ("0" * (point - len(digits)))
        else:
            rendered = coefficient[:point] + "." + coefficient[point:]
        return prefix + rendered
    if point >= -1000:
        return prefix + "0." + ("0" * max(0, -point)) + coefficient
    mantissa = coefficient[0]
    if len(coefficient) > 1:
        mantissa += "." + coefficient[1:]
    return f"{prefix}{mantissa}e{exponent + len(digits) - 1}"


@dataclass(frozen=True)
class Contract:
    max_reject_fraction: Decimal = Decimal("0.20")
    max_bytes: int = _HARD_MAX_BYTES
    max_rows: int = _HARD_MAX_ROWS

    def __post_init__(self) -> None:
        threshold = self.max_reject_fraction
        if isinstance(threshold, bool) or isinstance(threshold, float):
            raise TypeError("max_reject_fraction must be an exact decimal value")
        if isinstance(threshold, str):
            if not _THRESHOLD.fullmatch(threshold):
                raise ValueError("max_reject_fraction must be a plain decimal")
            try:
                threshold = Decimal(threshold)
            except InvalidOperation as exc:
                raise ValueError("max_reject_fraction must be a finite decimal") from exc
            object.__setattr__(self, "max_reject_fraction", threshold)
        elif isinstance(threshold, int):
            threshold = Decimal(threshold)
            object.__setattr__(self, "max_reject_fraction", threshold)
        elif not isinstance(threshold, Decimal):
            raise TypeError("max_reject_fraction must be Decimal or a decimal string")
        if not threshold.is_finite() or threshold < 0 or threshold > 1:
            raise ValueError("max_reject_fraction must be finite and between zero and one")
        for name, value in (("max_bytes", self.max_bytes), ("max_rows", self.max_rows)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    def to_dict(self) -> dict:
        """Return the complete stable policy used for batch identity."""
        return {
            "version": "sales-v1",
            "max_reject_fraction": _decimal_text(self.max_reject_fraction),
            "max_bytes": self.max_bytes,
            "max_rows": self.max_rows,
            "hard_limits": {"max_bytes": _HARD_MAX_BYTES, "max_rows": _HARD_MAX_ROWS},
            "headers": list(_HEADER),
            "rules": {
                "csv": {
                    "dialect": "excel",
                    "strict": True,
                    "field_limit": _HARD_MAX_BYTES,
                },
                "identifiers": {
                    "pattern": "ASCII letters, digits, dot, underscore, dash",
                    "min_length": 1,
                    "max_length": 64,
                },
                "quantity": {"min": 1, "max": 10000, "syntax": "ASCII decimal digits"},
                "unit_price": {
                    "min": "0.00",
                    "max": "1000000.00",
                    "fractional_digits": 2,
                    "syntax": "plain decimal",
                },
                "currency": {"allowed": ["USD", "EUR", "GBP"], "minor_unit_digits": 2},
                "ordered_at": {
                    "syntax": "YYYY-MM-DDTHH:MM:SS[.1-6 digits](Z|+/-HH:MM)",
                    "timezone": "UTC",
                    "suffix": "Z",
                },
                "duplicate_order_id": "quarantine every occurrence within the file",
                "blank_records": "ignored",
            },
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass
class RowResult:
    record_number: int
    raw: dict[str, str | list[str]]
    record: dict | None
    errors: list[dict]


@dataclass
class ValidationResult:
    file_sha256: str
    contract_fingerprint: str
    rows: list[RowResult] = field(default_factory=list)
    fatal_errors: list[dict] = field(default_factory=list)

    @property
    def error_fraction(self) -> Decimal:
        if not self.rows:
            return Decimal(0)
        rejected = sum(row.record is None for row in self.rows)
        return Decimal(rejected) / Decimal(len(self.rows))


def _issue(code: str, field_name: str, message: str) -> dict:
    return {"code": code, "field": field_name, "message": message}


def _fatal(code: str, field_name: str, message: str) -> dict:
    return _issue(code, field_name, message)


def _raw_mapping(headers: list[str], fields: list[str]) -> dict[str, str | list[str]]:
    raw: dict[str, str | list[str]] = {
        header: fields[index] for index, header in enumerate(headers) if index < len(fields)
    }
    if len(fields) > len(headers):
        raw["__extra__"] = fields[len(headers) :]
    return raw


def _parse_record(record_number: int, headers: list[str], fields: list[str]) -> RowResult:
    raw = _raw_mapping(headers, fields)
    errors: list[dict] = []
    if len(fields) != len(headers):
        errors.append(
            _issue("wrong_field_count", "__row__", "Record has the wrong number of fields.")
        )
        return RowResult(record_number, raw, None, errors)

    values = dict(zip(headers, fields, strict=True))
    canonical: dict = {}
    for name in ("order_id", "customer_id", "sku"):
        value = values[name]
        if not _IDENTIFIER.fullmatch(value):
            errors.append(
                _issue(
                    "invalid_identifier",
                    name,
                    "Identifier must use 1 to 64 allowed ASCII characters.",
                )
            )
        canonical[name] = value

    quantity_text = values["quantity"]
    if not _DIGITS.fullmatch(quantity_text):
        errors.append(
            _issue(
                "invalid_quantity", "quantity", "Quantity must be a whole number from 1 to 10000."
            )
        )
    else:
        significant_digits = quantity_text.lstrip("0") or "0"
        if len(significant_digits) > 5:
            errors.append(
                _issue(
                    "invalid_quantity",
                    "quantity",
                    "Quantity must be a whole number from 1 to 10000.",
                )
            )
        else:
            quantity = int(significant_digits)
            if not 1 <= quantity <= 10000:
                errors.append(
                    _issue(
                        "invalid_quantity",
                        "quantity",
                        "Quantity must be a whole number from 1 to 10000.",
                    )
                )
            else:
                canonical["quantity"] = quantity

    price_text = values["unit_price"]
    if not _PRICE.fullmatch(price_text):
        errors.append(
            _issue(
                "invalid_price",
                "unit_price",
                "Price must be a plain nonnegative decimal with at most two fractional digits.",
            )
        )
    else:
        whole_text, _, fraction_text = price_text.partition(".")
        significant_whole = whole_text.lstrip("0") or "0"
        if len(significant_whole) > 7 or (
            len(significant_whole) == 7 and significant_whole > "1000000"
        ):
            errors.append(
                _issue("invalid_price", "unit_price", "Price must not exceed 1000000.00.")
            )
        else:
            whole_minor = int(significant_whole) * 100
            fractional_minor = int(fraction_text.ljust(2, "0") or "0")
            minor_units = whole_minor + fractional_minor
            if minor_units > 100_000_000:
                errors.append(
                    _issue("invalid_price", "unit_price", "Price must not exceed 1000000.00.")
                )
            else:
                canonical["unit_price_minor"] = minor_units

    currency = values["currency"]
    if currency not in {"USD", "EUR", "GBP"}:
        errors.append(_issue("invalid_currency", "currency", "Currency must be USD, EUR, or GBP."))
    else:
        canonical["currency"] = currency

    timestamp = values["ordered_at"]
    try:
        if not _TIMESTAMP.fullmatch(timestamp):
            raise ValueError("timestamp does not match supported syntax")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.utcoffset() is None:
            raise ValueError("missing offset")
        normalized = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        canonical["ordered_at"] = normalized
    except (ValueError, OverflowError, OSError):
        errors.append(
            _issue(
                "invalid_timestamp",
                "ordered_at",
                "Timestamp must be valid ISO-8601 with an explicit UTC offset.",
            )
        )

    if errors:
        return RowResult(record_number, raw, None, errors)
    canonical["total_minor"] = canonical["quantity"] * canonical["unit_price_minor"]
    # Keep the public normalized shape in the plan's exact field order.
    canonical = {
        "order_id": canonical["order_id"],
        "ordered_at": canonical["ordered_at"],
        "customer_id": canonical["customer_id"],
        "sku": canonical["sku"],
        "quantity": canonical["quantity"],
        "unit_price_minor": canonical["unit_price_minor"],
        "currency": canonical["currency"],
        "total_minor": canonical["total_minor"],
    }
    return RowResult(record_number, raw, canonical, [])


def validate(data: bytes, contract: Contract) -> ValidationResult:
    """Validate bounded CSV bytes; fatal parser errors block the complete file."""
    result = ValidationResult(
        file_sha256=hashlib.sha256(data).hexdigest(),
        contract_fingerprint=contract.fingerprint(),
    )
    byte_limit = min(contract.max_bytes, _HARD_MAX_BYTES)
    if len(data) > byte_limit:
        result.fatal_errors.append(
            _fatal("file_too_large", "__file__", "Input exceeds the configured byte limit.")
        )
        return result
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.fatal_errors.append(_fatal("invalid_utf8", "__file__", "Input must be valid UTF-8."))
        return result

    try:
        with _bounded_csv_field_size():
            reader = csv.reader(io.StringIO(text, newline=""), dialect="excel", strict=True)
            header: list[str] | None = None
            for candidate in reader:
                if candidate:
                    header = candidate
                    break
            if header is None:
                result.fatal_errors.append(
                    _fatal("empty_file", "__file__", "Input contains no CSV header.")
                )
                return result
            if len(set(header)) != len(header):
                result.fatal_errors.append(
                    _fatal("duplicate_header", "__header__", "Header names must be unique.")
                )
                return result
            if len(header) != len(_HEADER) or set(header) != set(_HEADER):
                result.fatal_errors.append(
                    _fatal(
                        "invalid_header",
                        "__header__",
                        "Header must contain the seven sales fields exactly once.",
                    )
                )
                return result

            allowed_rows = min(contract.max_rows, _HARD_MAX_ROWS)
            data_count = 0
            for fields in reader:
                if not fields:  # A physically blank CSV record is ignored.
                    continue
                data_count += 1
                if data_count > allowed_rows:
                    result.fatal_errors.append(
                        _fatal(
                            "too_many_rows",
                            "__file__",
                            "Input exceeds the configured record limit.",
                        )
                    )
                    break
                result.rows.append(_parse_record(data_count + 1, header, fields))
    except csv.Error:
        result.fatal_errors.append(
            _fatal("malformed_csv", "__file__", "Input contains malformed CSV quoting.")
        )

    if not result.fatal_errors and not result.rows:
        result.fatal_errors.append(
            _fatal("empty_batch", "__file__", "Input contains a header but no data records.")
        )
        return result

    occurrences: dict[str, list[RowResult]] = {}
    for row in result.rows:
        order_id = row.raw.get("order_id")
        if isinstance(order_id, str) and order_id:
            occurrences.setdefault(order_id, []).append(row)
    for duplicates in occurrences.values():
        if len(duplicates) > 1:
            for row in duplicates:
                if not any(error["code"] == "duplicate_order_id" for error in row.errors):
                    row.errors.append(
                        _issue(
                            "duplicate_order_id",
                            "order_id",
                            "Order ID appears more than once in this file.",
                        )
                    )
                row.record = None
    return result
