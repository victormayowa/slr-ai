"""Record that a background process finished, for the administrator system page and scripts.production_check.

    uv run python -m scripts.record_heartbeat backup --detail size_bytes=123456
    uv run python -m scripts.record_heartbeat restore_drill

ops/backup.sh and ops/restore-drill.sh call it when they succeed.
"""

import argparse
import sys

import ops
from database import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", choices=["backup", "restore_drill", "worker"])
    parser.add_argument("--detail", action="append", default=[], help="key=value, repeatable")
    args = parser.parse_args()
    detail = dict(item.split("=", 1) for item in args.detail if "=" in item)
    with SessionLocal() as db:
        ops.heartbeat(db, args.name, detail)
    print(f"Recorded {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
