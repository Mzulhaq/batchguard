"""JSON CLI. Exit codes: 0 success, 1 operational error, 2 blocked batch."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from batchguard.contract import Contract
from batchguard.store import Warehouse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate CSV sales and publish audited facts")
    parser.add_argument("--db", default="batchguard.db", help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Validate and atomically publish one CSV batch")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--max-reject-fraction", default="0.20")
    for name in ("report", "quarantine"):
        commands.add_parser(name).add_argument("batch_id")
    commands.add_parser("summary", help="Published totals in minor units, grouped by currency")
    args = parser.parse_args(argv)
    try:
        # Validate policy and bound reads before opening or creating a database.
        if args.command == "ingest":
            contract = Contract(max_reject_fraction=args.max_reject_fraction)
            with args.file.open("rb") as handle:
                data = handle.read(contract.max_bytes + 1)
            if len(data) > contract.max_bytes:
                raise ValueError("Input exceeds the 5 MiB file limit")
        warehouse = Warehouse(args.db)
        if args.command == "ingest":
            result = warehouse.ingest(data, args.source, contract)
        elif args.command == "report":
            result = warehouse.report(args.batch_id)
            if result is None:
                raise KeyError("Batch not found")
        elif args.command == "quarantine":
            result = warehouse.quarantine(args.batch_id)
        else:
            result = warehouse.summary()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2 if args.command == "ingest" and result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
