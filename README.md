# Hemzo
ML-powered shortage forecasting for India's blood banks, built on live eRaktKosh data.

## How the live system works

```
GitHub Actions (wakes hourly at :07 UTC; pipeline/gate.py lets it through when the last snapshot is >= 3.5 h old)
  scrape.py            fetch eRaktKosh  ->  data/raw/<timestamp>.json      (commit 1: raw snapshot)
  python -m pipeline.run   read ALL of data/raw/ -> train + backtest -> web/data/forecast.json   (commit 2)
web/*.html             fetch data/forecast.json in the browser and draw the map, charts and alerts
```

- **`pipeline/panel.py`** turns snapshots into units per (component, district, blood group) on a regular 6 h grid.
- **`pipeline/features.py`** builds level / trend / rhythm features, all divided by each series' own level.
- **`pipeline/model.py`** trains a pooled gradient-boosted model, backtests it with forward-chaining folds against a
  "tomorrow = today" baseline, and deploys whichever is better per horizon. It also scores the chance of a series
  dropping below 55% of its usual (14-day median) level within 3 days.
- **`pipeline/run.py`** writes `web/data/forecast.json`: forecasts, 80% bands, risk scores, and the metrics the
  Data Sources page shows. Output is deterministic for a given archive.

**Why hourly wake-ups?** GitHub's scheduler is best-effort: a plain `0 */4 * * *` cron was in practice running about every
6 hours and once went 16 hours without firing. Waking hourly and gating on the age of the newest snapshot keeps the cycle
near 4 hours, and one dropped trigger costs an hour rather than a whole cycle. The numbers live in
`pipeline/schedule.py`, are published in `forecast.json` (`meta.schedule`), and drive the live countdown bar
(`web/js/pulse.js`) at the top of every page. Add `?pulseNow=2026-10-18T03:30:00Z` to any page to see the bar at a
pretend time.

Run it locally:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pipeline.run                       # rebuild web/data/forecast.json
python3 -m http.server 8743 --directory web            # then open http://localhost:8743
```

Honest status: with ~5 weeks of history the model does not beat the naive baseline out-of-sample, so the baseline is
what is deployed; the pipeline re-checks this on every run and switches on its own once the model earns it.
