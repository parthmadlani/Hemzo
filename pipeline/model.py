"""Train, backtest and forecast.

Design in one paragraph: one pooled gradient-boosted model is trained on every
(origin, series) pair to predict the change in units h days ahead (h = 1..7), working
in 'relative' units (divided by the series' own recent level) so a 3-unit whole-blood
series and a 2,000-unit red-cell series can share a model. Every run it is backtested
with forward-chaining folds against a persistence baseline ('tomorrow = today'), and
for each horizon the pipeline deploys whichever is better. With only weeks of history
the baseline often wins; as the archive grows the model takes over on its own. Interval
bands come from out-of-fold residuals, so they widen exactly as much as the model has
actually been wrong. A second classifier scores the chance that a series drops below 55%
of its usual level within 3 days.
"""

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score, roc_auc_score

from .features import D, FEATURES, HORIZONS

warnings.filterwarnings("ignore")

CRITICAL = 0.55      # cover below this = critical (cover = units / own 14-day median)
FOLD_DAYS = 3        # length of each out-of-time test window
N_FOLDS = 4          # test windows, stacked back from the newest data
MIN_GAIN = 0.02      # model must beat persistence by 2% (relative MAE) to be deployed
MIN_TRAIN_ROWS = 1500
MIN_LEVEL = 3        # series whose usual level is below this many units are labelled "sparse"
RISK_H = 3           # horizon (days) of the shortage-risk score
SCALED = ["x0", "d1", "d2", "d3", "d7", "std7", "min7", "max7", "med14", "district_total"]
FEAT = FEATURES + ["logscale"]

REG_PARAMS = dict(loss="absolute_error", max_iter=120, learning_rate=0.05, max_depth=3,
                  min_samples_leaf=100, l2_regularization=5.0, random_state=0)
CLF_PARAMS = dict(max_iter=120, learning_rate=0.05, max_depth=3, min_samples_leaf=80,
                  l2_regularization=5.0, random_state=0)


def scale_of(X: pd.DataFrame) -> pd.Series:
    return X["mean7"] + 1.0


def scaled_features(X: pd.DataFrame, S: pd.Series) -> pd.DataFrame:
    Z = X[FEATURES].copy()
    for c in SCALED:
        Z[c] = X[c] / S
    Z["logscale"] = np.log(S)
    return Z[FEAT]


def _windows(origin_times, end):
    """Out-of-time test windows (cut, cut+FOLD_DAYS], oldest first."""
    return [end - pd.Timedelta(days=FOLD_DAYS * k) for k in range(N_FOLDS, 0, -1)]


def backtest(X: pd.DataFrame, end: pd.Timestamp) -> dict:
    """Forward-chaining evaluation. For each horizon h and fold: train only on origins whose
    outcome was already known at the fold's cut, test on the next FOLD_DAYS of origins."""
    T = X.index.get_level_values(0)
    S = scale_of(X)
    Z = scaled_features(X, S)
    out = {"folds": {}, "resid": {}, "skill": {}, "n_test": {}}
    for h in HORIZONS:
        y = X[f"y{h}"]
        rel_rows = []   # per-fold dicts
        resid_model, resid_pers = [], []
        for cut in _windows(T, end):
            trm = (T <= cut - pd.Timedelta(days=h)) & y.notna().values
            tem = (T > cut) & (T <= cut + pd.Timedelta(days=FOLD_DAYS)) & y.notna().values
            if tem.sum() < 200 or trm.sum() < MIN_TRAIN_ROWS:
                continue
            mdl = HistGradientBoostingRegressor(**REG_PARAMS).fit(Z[trm], (y / S)[trm])
            rel_m = (y[tem] / S[tem]).values - mdl.predict(Z[tem])   # model error, relative
            rel_p = (y[tem] / S[tem]).values                          # persistence error, relative
            rel_rows.append(dict(cut=cut, n=int(tem.sum()), model=np.abs(rel_m).mean(), persist=np.abs(rel_p).mean()))
            resid_model.append(rel_m)
            resid_pers.append(rel_p)
        out["folds"][h] = rel_rows
        out["resid"][h] = {"model": resid_model, "persist": resid_pers}
    return out


def choose_methods(bt: dict) -> dict:
    """Per horizon: deploy the model only if it beats persistence by MIN_GAIN out-of-sample."""
    chosen = {}
    for h in HORIZONS:
        rows = bt["folds"][h]
        if not rows:
            chosen[h] = dict(method="persistence", model_err=None, persist_err=None, gain=None, n_test=0, folds=0)
            continue
        n = np.array([r["n"] for r in rows], float)
        me = float(np.average([r["model"] for r in rows], weights=n))
        pe = float(np.average([r["persist"] for r in rows], weights=n))
        gain = (pe - me) / pe if pe > 0 else 0.0
        chosen[h] = dict(method="model" if gain >= MIN_GAIN else "persistence",
                         model_err=me, persist_err=pe, gain=gain, n_test=int(n.sum()), folds=len(rows))
    return chosen


def band_quantiles(bt: dict, chosen: dict) -> dict:
    """Relative 10th/90th residual percentiles per horizon from the chosen method's out-of-fold
    errors, plus leave-one-fold-out coverage of the resulting band (how often truth landed inside)."""
    q, cov = {}, {}
    for h in HORIZONS:
        key = "model" if chosen[h]["method"] == "model" else "persist"
        parts = bt["resid"][h][key]
        if not parts:
            q[h], cov[h] = (-0.5, 0.5), None
            continue
        allr = np.concatenate(parts)
        q[h] = (float(np.quantile(allr, 0.1)), float(np.quantile(allr, 0.9)))
        if len(parts) >= 2:
            inside, total = 0, 0
            for i, p in enumerate(parts):
                rest = np.concatenate([x for j, x in enumerate(parts) if j != i])
                lo, hi = np.quantile(rest, 0.1), np.quantile(rest, 0.9)
                inside += int(((p >= lo) & (p <= hi)).sum())
                total += len(p)
            cov[h] = inside / total
        else:
            cov[h] = None
    return {"q": q, "coverage": cov}


def risk_backtest(X: pd.DataFrame, end: pd.Timestamp) -> dict:
    """Shortage-onset classifier vs the plain 'how low is it already' baseline, on series that are
    not critical yet, forward-chained like the regressors."""
    h = RISK_H
    T = X.index.get_level_values(0)
    S = scale_of(X)
    Z = scaled_features(X, S)
    ok = X[f"y{h}"].notna().values & (X["med14"] >= 3).values
    cover_now = X["x0"] / X["med14"].clip(lower=1)
    label = (((X["x0"] + X[f"y{h}"]) / X["med14"].clip(lower=1)) < CRITICAL).astype(int)
    already = (cover_now < CRITICAL).values
    aucs_m, aucs_b, aps_m, aps_b, npos, ntest = [], [], [], [], 0, 0
    for cut in _windows(T, end):
        trm = (T <= cut - pd.Timedelta(days=h)) & ok
        tem = (T > cut) & (T <= cut + pd.Timedelta(days=FOLD_DAYS)) & ok & ~already
        if tem.sum() < 300 or label[tem].sum() < 5 or trm.sum() < MIN_TRAIN_ROWS or label[trm].nunique() < 2:
            continue
        clf = HistGradientBoostingClassifier(**CLF_PARAMS).fit(Z[trm], label[trm])
        p = clf.predict_proba(Z[tem])[:, 1]
        b = -cover_now[tem].values
        aucs_m.append(roc_auc_score(label[tem], p)); aucs_b.append(roc_auc_score(label[tem], b))
        aps_m.append(average_precision_score(label[tem], p)); aps_b.append(average_precision_score(label[tem], b))
        npos += int(label[tem].sum()); ntest += int(tem.sum())
    if not aucs_m:
        return {"available": False}
    return dict(available=True, auc_model=float(np.mean(aucs_m)), auc_baseline=float(np.mean(aucs_b)),
                ap_model=float(np.mean(aps_m)), ap_baseline=float(np.mean(aps_b)),
                n_test=ntest, n_events=npos, folds=len(aucs_m), horizon_days=h)


def forecast_latest(X: pd.DataFrame, chosen: dict, bands: dict):
    """Fit on everything known, predict from the newest origin. Returns (paths, risk) keyed by series id."""
    T = X.index.get_level_values(0)
    last = T.max()
    latest = X[T == last]
    S_all = scale_of(X)
    Z_all = scaled_features(X, S_all)
    S_l = scale_of(latest)
    Z_l = scaled_features(latest, S_l)
    sids = latest.index.get_level_values(1)

    delta = {}
    for h in HORIZONS:
        if chosen[h]["method"] == "model":
            known = X[f"y{h}"].notna().values
            mdl = HistGradientBoostingRegressor(**REG_PARAMS).fit(Z_all[known], (X[f"y{h}"] / S_all)[known])
            delta[h] = mdl.predict(Z_l) * S_l.values
        else:
            delta[h] = np.zeros(len(latest))

    paths = {}
    for i, sid in enumerate(sids):
        x0 = float(latest["x0"].iloc[i])
        s = float(S_l.iloc[i])
        fc, lo, hi = [x0], [x0], [x0]
        for h in HORIZONS:
            mid = max(0.0, x0 + delta[h][i])
            qlo, qhi = bands["q"][h]
            fc.append(mid)
            lo.append(max(0.0, mid + qlo * s))
            hi.append(max(mid, mid + qhi * s))
        paths[sid] = (fc, lo, hi)

    # shortage-risk classifier
    risk = {}
    h = RISK_H
    ok = X[f"y{h}"].notna().values & (X["med14"] >= 3).values
    label = (((X["x0"] + X[f"y{h}"]) / X["med14"].clip(lower=1)) < CRITICAL).astype(int)
    if ok.sum() >= MIN_TRAIN_ROWS and label[ok].nunique() == 2:
        clf = HistGradientBoostingClassifier(**CLF_PARAMS).fit(Z_all[ok], label[ok])
        pr = clf.predict_proba(Z_l)[:, 1]
        risk = {sid: float(p) for sid, p in zip(sids, pr)}
    return paths, risk, last
