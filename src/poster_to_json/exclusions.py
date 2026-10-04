"""Records that are never delivered, with the reason for each.

Some deposits typed as posters are not research posters at all (customer
service phone number spam, vendor product sheets). The platform tombstones
them, but a tombstone does not stop the next harvest from bringing them back,
so the pipeline skips them here. Each entry is a reviewed decision recorded in
excluded_records.csv; nothing is excluded by heuristic.
"""

import csv
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

_CSV = Path(__file__).with_name("excluded_records.csv")


@lru_cache(maxsize=1)
def excluded_records() -> Dict[Tuple[str, str], str]:
    """{(repository, record_id): reason} from excluded_records.csv."""
    out = {}
    with open(_CSV, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            repo = (row.get("repository") or "").strip().lower()
            rid = (row.get("record_id") or "").strip()
            if repo and rid:
                out[(repo, rid)] = (row.get("reason") or "").strip()
    return out


def exclusion_reason(repository: str, record_key: str):
    """The reason a record is excluded, or None.

    record_key may be a merged file stem such as "903710.v1" or
    "16960815_complete"; versions of an excluded record are excluded too.
    """
    rid = str(record_key).split("_", 1)[0].split(".", 1)[0]
    return excluded_records().get((str(repository).strip().lower(), rid))
