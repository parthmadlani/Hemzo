"""Turn the raw eRaktKosh snapshots in data/raw/ into a regular time grid of
units-on-hand per (component, district, blood group)."""

import glob
import json
import os

import numpy as np
import pandas as pd

GROUPS = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
GRID = "6h"  # scraper runs roughly every 4-6h; one grid step = 6h
STEPS_PER_DAY = 4


def district_of(address: str) -> str:
    """eRaktKosh addresses end in '..., <district>, <state>'."""
    parts = [p.strip() for p in (address or "").split(",")]
    return parts[-2] if len(parts) >= 2 and parts[-2] else "Unknown"


def load_long(raw_dir: str) -> pd.DataFrame:
    """One row per (snapshot, component, bank, blood group) with a unit count.

    A bank that is 'not_available' has no rows: that is stock of zero, which the
    wide pivot below turns into 0 for every group of that bank's district.
    A group listed without a number ('AB +ve') is counted as 1 unit (the
    minimum that 'available' can mean).
    """
    rows, bank_rows = [], []
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        snap = json.load(open(path))
        t = pd.Timestamp(snap["fetched_at"]).tz_convert("UTC").tz_localize(None)
        for g in snap["groups"]:
            for b in g["banks"]:
                key = b["address"] if b["name"].strip() in ("", "-") else b["name"]
                dist = district_of(b["address"])
                bank_rows.append((t, g["state"], g["component"], key, dist, b["status"] == "available"))
                for grp, n in b["groups"].items():
                    rows.append((t, g["state"], g["component"], key, dist, grp, 1 if n is None else n))
    banks = pd.DataFrame(bank_rows, columns=["t", "state", "component", "bank", "district", "available"])
    units = pd.DataFrame(rows, columns=["t", "state", "component", "bank", "district", "group", "units"])
    return banks, units


def build_wide(raw_dir: str):
    """Returns (wide, n_banks, meta).

    wide:    DataFrame indexed by 6h grid time, columns = (component, district, group), values = units
    n_banks: DataFrame, same index, columns = (component, district), number of banks that listed the
             district at that snapshot (the denominator for 'how many banks are we actually seeing')
    """
    banks, units = load_long(raw_dir)
    units = units[units.group.isin(GROUPS)]
    agg = units.groupby(["t", "component", "district", "group"]).units.sum().unstack(["component", "district", "group"])
    all_t = pd.Index(sorted(banks.t.unique()))
    # a district's group not appearing in a snapshot means 0 units, but only for districts that exist
    agg = agg.reindex(all_t)
    districts = banks.groupby("component").district.unique()
    cols = [(c, d, g) for c in districts.index for d in sorted(districts[c]) for g in GROUPS]
    wide = agg.reindex(columns=pd.MultiIndex.from_tuples(cols, names=["component", "district", "group"])).fillna(0.0)

    nb = banks.groupby(["t", "component", "district"]).bank.nunique().unstack(["component", "district"]).reindex(all_t)
    # regular grid; carry the last observation forward across snapshot gaps (max gap is ~16h)
    grid = pd.date_range(all_t[0].floor(GRID), all_t[-1].ceil(GRID), freq=GRID)
    wide = wide.reindex(wide.index.union(grid)).sort_index().ffill().reindex(grid)
    nb = nb.reindex(nb.index.union(grid)).sort_index().ffill().reindex(grid)
    latest = banks[banks.t == all_t[-1]]
    meta = {
        "first": all_t[0], "last": all_t[-1], "n_snapshots": len(all_t),
        "banks_listed": int(latest.groupby("component").bank.nunique().max()),
        "banks_reporting": int(latest[latest.available].bank.nunique()),
        "state": banks.state.iloc[-1],
    }
    return wide, nb, meta
