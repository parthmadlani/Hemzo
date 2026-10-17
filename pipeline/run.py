"""Entry point:  python -m pipeline.run

Reads data/raw/*.json (the scraper's snapshots), trains + backtests the model, and writes
web/data/forecast.json, the only file the website reads.
"""

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from . import model as M
from . import schedule
from .districts import COORDS
from .features import D, HORIZONS, feature_cube, flatten, stack_rows
from .panel import GROUPS, build_wide

IST = pd.Timedelta(hours=5, minutes=30)
THRESHOLDS = (CRITICAL := M.CRITICAL, 0.85, 1.35)


def status_of(cover: float) -> str:
    return "critical" if cover < THRESHOLDS[0] else "low" if cover < THRESHOLDS[1] else "steady" if cover < THRESHOLDS[2] else "surplus"


def r1(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 1)


def main(raw_dir="data/raw", out_path="web/data/forecast.json"):
    t0 = time.time()
    wide, nb, pmeta = build_wide(raw_dir)
    w, nbf, dtot, meta = flatten(wide, nb)
    X = stack_rows(feature_cube(w, nbf, dtot, meta), w)
    end = w.index[-1]

    bt = M.backtest(X, end)
    chosen = M.choose_methods(bt)
    bands = M.band_quantiles(bt, chosen)
    risk_eval = M.risk_backtest(X, end)
    paths, risk, last = M.forecast_latest(X, chosen, bands)

    # observed history for the chart: the same hour of day, 13..0 days ago
    obs_idx = [len(w) - 1 - D * k for k in range(13, -1, -1)]
    latest = X[X.index.get_level_values(0) == last].droplevel(0)

    comps = sorted(wide.columns.get_level_values(0).unique())
    state = pmeta["state"]
    coords = COORDS.get(state, {})
    districts = []
    all_d = sorted({d for _, d in wide.columns.droplevel(2).unique()})
    nb_last = nb.iloc[-1]
    for dname in all_d:
        entry = {"name": dname, "lat": coords.get(dname, (None, None))[0], "lon": coords.get(dname, (None, None))[1],
                 "banks": int(max(nb_last.get((c, dname), 0) or 0 for c in comps)), "components": {}}
        for comp in comps:
            groups = {}
            for g in GROUPS:
                sid = f"{comp}|{dname}|{g}"
                if sid not in paths:
                    groups[g] = {"nodata": True}
                    continue
                fc, lo, hi = paths[sid]
                row = latest.loc[sid]
                sparse = float(row["med14"]) < M.MIN_LEVEL   # too few units for a trend to mean anything
                ref = max(float(row["med14"]), 1.0)
                units = float(row["x0"])
                cover = units / ref
                floor = CRITICAL * ref
                days_to_floor = None
                if sparse:
                    days_to_floor = None
                elif units < floor:
                    days_to_floor = 0
                else:
                    for h in HORIZONS:
                        if fc[h] < floor:
                            days_to_floor = h
                            break
                groups[g] = {
                    "units": int(round(units)), "ref": r1(ref), "floor": r1(floor), "cover": round(cover, 2),
                    "status": "sparse" if sparse else status_of(cover),
                    "obs": [int(round(w[sid].iloc[i])) for i in obs_idx],
                    "fc": [int(round(v)) for v in fc], "lo": [int(round(v)) for v in lo], "hi": [int(round(v)) for v in hi],
                    "delta": int(round((fc[7] - fc[0]) / max(fc[0], 1.0) * 100)),
                    "risk3": None if sparse or sid not in risk else round(risk[sid], 3),
                    "days_to_floor": days_to_floor,
                    "low_volume": bool(ref < 5),
                }
            entry["components"][comp] = groups
        districts.append(entry)

    n_days = (pmeta["last"] - pmeta["first"]).total_seconds() / 86400
    horizons = {}
    for h in HORIZONS:
        c = chosen[h]
        horizons[str(h)] = {
            "method": c["method"], "model_error": r1(c["model_err"] * 100) if c["model_err"] is not None else None,
            "baseline_error": r1(c["persist_err"] * 100) if c["persist_err"] is not None else None,
            "gain_pct": r1(c["gain"] * 100) if c["gain"] is not None else None,
            "band_coverage": r1(bands["coverage"][h] * 100) if bands["coverage"][h] is not None else None,
            "n_test": c["n_test"], "folds": c["folds"],
        }
    out = {
        "meta": {
            "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_through": (pmeta["last"] + IST).strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "first_snapshot": (pmeta["first"] + IST).strftime("%Y-%m-%d"),
            "history_days": round(n_days, 1), "snapshots": pmeta["n_snapshots"],
            "state": state, "banks_listed": pmeta["banks_listed"], "banks_reporting": pmeta["banks_reporting"],
            "districts": len(districts), "series_modelled": int(len(paths)),
            "components": comps, "groups": GROUPS, "thresholds": {"critical": THRESHOLDS[0], "low": THRESHOLDS[1], "surplus": THRESHOLDS[2]},
            "schedule": schedule.as_dict(),
        },
        "metrics": {
            "unit": "mean absolute error as % of each series' own recent level; lower is better",
            "horizons": horizons,
            "shortage_risk": risk_eval,
            "training_rows": int(len(X)), "fold_days": M.FOLD_DAYS, "folds": M.N_FOLDS, "min_gain_to_deploy_model": M.MIN_GAIN * 100,
            "algorithm": "HistGradientBoosting (pooled, scale-normalised) vs persistence baseline",
        },
        "districts": districts,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False)
    print(f"wrote {out_path} ({os.path.getsize(out_path)/1024:.0f} KB) in {time.time()-t0:.1f}s; "
          f"data through {out['meta']['data_through']}; methods: " +
          ", ".join(f"h{h}={chosen[h]['method']}" for h in HORIZONS))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="web/data/forecast.json")
    a = ap.parse_args()
    main(a.raw, a.out)
