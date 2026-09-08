# Horizon — Renewal Forecasting

Machine-learning forecast of renewal likelihood for the Crumdale HPS Level-Funded &
Self-Funded book, retrained **monthly** so predictive quality improves as new renewal
decisions arrive.

Built from the kickoff brief: predict renewal success from recent BOR activity, broker
quality, group experience, loss ratio, initial underwriting RTM, RSD/AM assignment,
lasers at sale and at renewal, tenure with Crumdale, renewal rate action, and service
friction.

## Architecture

```
ForecastEngine/
├── backend/                 FastAPI + scikit-learn
│   ├── app/
│   │   ├── real_mode.py         serves the real book: state, rescoring, name matching
│   │   ├── insights.py          risk tiers, reason codes, quarterly forecast rollups
│   │   ├── forward_book.py      forward-test window bookkeeping
│   │   ├── data_quality.py      Renewal Database / training-data health report
│   │   └── main.py              REST API (serves the built frontend too)
│   ├── etl_real.py              ETL: raw exec-log/tracker exports -> real_history.csv
│   │                             + real_active_book.csv
│   ├── train_real.py            trains the production ensemble on real_history.csv
│   ├── build_book.py            rebuilds the scored book from sf_deals.csv + the
│   │                             two files above, backfilling anything Salesforce
│   │                             doesn't have coverage for yet
│   ├── data/                    real_history.csv, real_active_book.csv, sf_deals.csv,
│   │                             real_scored_book.csv (what real_mode serves)
│   └── models/                  versioned model artifacts + registry.json
└── frontend/                React + Vite + Recharts dashboard
```

### The model

* **Ensemble**: soft-voting Random Forest + Extra Trees + Histogram Gradient Boosting —
  three diverse tree learners that capture the non-linear renewal dynamics (loss-ratio
  cliffs, first-renewal churn, BOR shocks).
* **Calibrated probabilities**: isotonic calibration (3-fold) so "82% likely to renew"
  really means 82% — which is what lets per-group scores roll up into a defensible
  premium-weighted retention forecast.
* **Honest validation**: metrics are reported on a leak-free walk-forward backtest
  (`walk_forward.py`/`experiment.py`), not just an in-sample holdout — the model is
  never graded on data it could have memorized.
* **Dynamic monthly cadence**: every retrain appends a version to `models/registry.json`.
* **Explainability**: permutation importance ranks the business factors by measured
  predictive power, and every group gets plain-English reason codes ("BOR change in the
  last 12 months", "Loss ratio 97% — running hot").

## Run it

```powershell
# 1. Backend deps (once)
cd backend
python -m pip install -r requirements.txt

# 2. Build the real data + train (see backend/etl_real.py for the raw source files it expects)
python etl_real.py
python train_real.py
python build_book.py

# 3. Frontend (once)
cd ..\frontend
npm install
npm run build

# 4. Serve everything from one process
cd ..\backend
python -m uvicorn app.main:app --port 8000
```

Startup fails loudly if `data/real_scored_book.csv` / `models/real_latest.joblib` aren't
present — there's no fallback demo mode, so a broken pipeline can't be masked by fake
data. Run the three steps above first.

Open **http://localhost:8000** — the API serves the built dashboard.

For frontend development with hot reload: `npm run dev` in `frontend/` (proxies `/api`
to port 8000).

`POST /api/retrain` runs `train_real.py` + `build_book.py` and hot-swaps the served
state — this is what the dashboard's "Monthly Retrain" button calls.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/summary` | KPI rollup for the forecast window |
| `GET /api/forecast` | monthly + quarterly expected renewals / premium at risk |
| `GET /api/groups` | every group due in the window, scored, with reason codes |
| `GET /api/segments` | retention by LOB, RSD, broker tier, tenure |
| `GET /api/data-quality` | training-data health report |
| `GET /api/renewal-database` | every historical renewal decision, row-level |
| `GET /api/model/metrics` | current metrics + full registry history |
| `GET /api/model/importance` | permutation feature importance |
| `GET /api/model/diagnostics` | calibration curve + ROC points |
| `POST /api/retrain` | monthly refresh: retrain, rebuild the book, rescore |
| `POST /api/groups/move-decided-to-database` | move every decided group into the Renewal Database |
