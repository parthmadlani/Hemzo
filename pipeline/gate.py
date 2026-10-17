"""Decide whether this workflow wake-up should scrape. Prints and (inside GitHub Actions) exports
run=true|false. Standard library only, so the gate job needs no dependencies and a skipped
wake-up finishes in seconds.

    python -m pipeline.gate [--raw data/raw]

A manual run (workflow_dispatch) always goes ahead.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

from .schedule import MIN_GAP_MINUTES

NAME = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")


def newest_snapshot(raw_dir):
    """UTC time of the newest snapshot, read from the file name (…T18-26-51.0+00-00.json)."""
    best = None
    for f in os.listdir(raw_dir) if os.path.isdir(raw_dir) else []:
        m = NAME.search(f)
        if m:
            t = datetime.strptime(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%dT%H:%M:%S")
            t = t.replace(tzinfo=timezone.utc)
            if best is None or t > best:
                best = t
    return best


def decide(raw_dir, event, now=None):
    now = now or datetime.now(timezone.utc)
    if event == "workflow_dispatch":
        return True, "manual run"
    last = newest_snapshot(raw_dir)
    if last is None:
        return True, "no snapshot yet"
    age = (now - last).total_seconds() / 60
    if age >= MIN_GAP_MINUTES:
        return True, f"newest snapshot is {age:.0f} min old (>= {MIN_GAP_MINUTES})"
    return False, f"newest snapshot is only {age:.0f} min old (< {MIN_GAP_MINUTES}); skipping"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    args = ap.parse_args(argv)
    run, why = decide(args.raw, os.environ.get("GITHUB_EVENT_NAME", ""))
    print(f"run={'true' if run else 'false'}  ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
