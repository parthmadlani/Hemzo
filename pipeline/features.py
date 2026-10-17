"""Feature construction for the pooled forecaster.

Every (component, district, blood group) series is a column of the 6h grid.
An 'origin' is one grid time; the model predicts how many units the series
will have h days after that origin, expressed as a change from now.
"""

import numpy as np
import pandas as pd

from .panel import GROUPS, STEPS_PER_DAY

HORIZONS = list(range(1, 8))  # days ahead
D = STEPS_PER_DAY
FEATURES = [
    "x0", "d1", "d2", "d3", "d7", "mean7", "std7", "min7", "max7", "med14",
    "cover", "changes7", "since_change", "n_banks", "district_total", "group", "component",
]


def flatten(wide: pd.DataFrame, n_banks: pd.DataFrame):
    """Keep only series that ever showed stock and give each a flat string id.

    Returns (w, nb, meta): w/nb share columns = series id; meta maps id -> component/district/group.
    """
    keep = wide.columns[(wide > 0).any().values]
    ids = [f"{c}|{d}|{g}" for c, d, g in keep]
    w = wide[keep].copy()
    w.columns = ids
    meta = pd.DataFrame(list(keep), index=ids, columns=["component", "district", "group"])
    nb = pd.DataFrame({i: n_banks[(c, d)] for i, (c, d, g) in zip(ids, keep)})
    dt = w.T.groupby([meta.component.values, meta.district.values]).sum().T
    dtot = pd.DataFrame({i: dt[(c, d)] for i, (c, d, g) in zip(ids, keep)})
    return w, nb, dtot, meta


def feature_cube(w: pd.DataFrame, nb: pd.DataFrame, dtot: pd.DataFrame, meta: pd.DataFrame) -> dict:
    """Every feature for every (origin, series) as aligned frames. Origins without a
    week of history are NaN and dropped in stack_rows."""
    f = {"x0": w}
    for name, k in (("d1", D), ("d2", 2 * D), ("d3", 3 * D), ("d7", 7 * D)):
        f[name] = w - w.shift(k)
    r7 = w.rolling(7 * D, min_periods=7 * D)
    f["mean7"], f["std7"], f["min7"], f["max7"] = r7.mean(), r7.std(), r7.min(), r7.max()
    f["med14"] = w.rolling(14 * D, min_periods=7 * D).median()
    f["cover"] = w / f["med14"].where(f["med14"] > 0)
    changed = (w.diff().fillna(0) != 0).astype(float)
    f["changes7"] = changed.rolling(7 * D, min_periods=7 * D).sum()
    pos = pd.DataFrame(np.repeat(np.arange(len(w))[:, None], w.shape[1], axis=1), index=w.index, columns=w.columns)
    last_move = pos.where(changed > 0).ffill().fillna(0)
    f["since_change"] = (pos - last_move).clip(upper=14 * D)
    f["n_banks"] = nb
    f["district_total"] = dtot
    f["group"] = pd.DataFrame(
        np.tile([GROUPS.index(g) for g in meta.group], (len(w), 1)), index=w.index, columns=w.columns)
    f["component"] = pd.DataFrame(
        np.tile((meta.component == "Packed RBC").astype(int).values, (len(w), 1)), index=w.index, columns=w.columns)
    return f


def stack_rows(f: dict, w: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """Long table: one row per (origin, series) with features and y{h} = units(t + h days) - units(t)."""
    X = pd.concat({k: v.stack() for k, v in f.items()}, axis=1)
    X.index.names = ["t", "sid"]
    for h in horizons:
        X[f"y{h}"] = (w.shift(-h * D) - w).stack(future_stack=True).reindex(X.index)
    return X.dropna(subset=["mean7", "med14", "std7"])
