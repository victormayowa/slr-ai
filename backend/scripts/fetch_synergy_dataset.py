"""Build a screening benchmark data set from a SYNERGY data set (https://github.com/asreview/synergy-dataset).

SYNERGY provides the records and inclusion labels of published systematic reviews; it doesn't include their eligibility
criteria, so give the review's criteria in a text file (one criterion per line, prefixed "include:" or "exclude:").

    uv run --with synergy-dataset python -m scripts.fetch_synergy_dataset Appenzeller-Herzog_2019 criteria.txt \
        --limit 500

The data set is written to BENCHMARK_DATASETS_DIR (default backend/benchmarks/datasets). Check each data set's licence
in the SYNERGY repository before use.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

from benchmarks import DATASETS_DIR, validate_dataset


def read_criteria(path: Path) -> list[dict[str, str]]:
    criteria = []
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*(include|exclude)\s*:\s*(.+)$", line, re.I)
        if match:
            criteria.append(
                {
                    "kind": "inclusion" if match.group(1).lower() == "include" else "exclusion",
                    "text": match.group(2).strip(),
                }
            )
    return criteria


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="SYNERGY data set name, for example Appenzeller-Herzog_2019")
    parser.add_argument("criteria", type=Path)
    parser.add_argument(
        "--limit", type=int, default=0, help="Sample at most this many records, keeping every inclusion"
    )
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()
    try:
        from synergy_dataset import Dataset  # type: ignore[import-not-found]
    except ImportError:
        print("Install the synergy-dataset package: uv run --with synergy-dataset ...", file=sys.stderr)
        return 1
    frame = Dataset(args.name).to_frame()
    rows = [
        {
            "id": str(index),
            "title": str(row.get("title") or ""),
            "abstract": str(row.get("abstract") or ""),
            "label": bool(row.get("label_included")),
        }
        for index, row in frame.iterrows()
        if row.get("title")
    ]
    if args.limit and len(rows) > args.limit:
        included = [r for r in rows if r["label"]]
        excluded = [r for r in rows if not r["label"]]
        random.Random(args.seed).shuffle(excluded)
        rows = included + excluded[: max(0, args.limit - len(included))]
    key = re.sub(r"[^a-z0-9_-]+", "-", f"synergy-{args.name}".lower())
    content = {
        "key": key,
        "name": f"SYNERGY {args.name}",
        "task": "screening",
        "description": f"Records and inclusion labels from the SYNERGY data set {args.name}.",
        "source": "https://github.com/asreview/synergy-dataset",
        "license": "See the SYNERGY repository for this data set's licence",
        "criteria": read_criteria(args.criteria),
        "items": rows,
    }
    validate_dataset(content)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    target = DATASETS_DIR / f"{key}.json"
    target.write_text(json.dumps(content))
    print(f"Wrote {len(rows)} records to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
