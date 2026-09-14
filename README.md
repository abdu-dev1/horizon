# Horizon — Renewal & New Business Forecasting

Two sibling machine-learning products for the Crumdale HPS book, served as one app:

* **Renewals** — forecasts renewal likelihood for the existing Level-Funded & Self-Funded
  book, retrained **monthly** so predictive quality improves as new renewal decisions
  arrive. Built from the kickoff brief: predict renewal success from recent BOR activity,
  broker quality, group experience, loss ratio, initial underwriting RTM, RSD/AM
  assignment, lasers at sale and at renewal, tenure with Crumdale, renewal rate action, and
  service friction.
* **New Business** — forecasts win likelihood for open quotes on first-time clients (not
  renewals). A separate model, separate data, separate underwriting questions — but the
  same audience wants both side by side, so it ships as one product with a switcher at
  the top of the dashboard rather than two apps to log into.

## Architecture

```
ForecastEngine/
├── gateway/                  The ONLY public-facing process. Everything below is
│   │                          reached through it — see "Run it".
│   ├── run_app.py                 starts both engines below + the gateway together;
│   │                              this is the one entrypoint you actually run
│   ├── main.py                    reverse proxy: /nb-app/* -> New Business engine,
│   │                              everything else (incl. /api/*, /, static assets)
│   │                              -> Renewal engine
│   └── auth.py                    identity + admin policy, resolved ONCE here so
│                                  neither engine needs its own auth code — see
│                                  "Auth" below
│
├── backend/                  Renewals engine — FastAPI + scikit-learn, internal
│   │                          port 8010 (never reachable directly outside dev)
│   ├── app/
│   │   ├── real_mode.py          serves the real book: state, rescoring, name matching
│   │   ├── insights.py           risk tiers, reason codes, quarterly forecast rollups
│   │   ├── forward_book.py       forward-test window bookkeeping
│   │   ├── data_quality.py       Renewal Database / training-data health report
│   │   └── main.py               REST API (also serves the built frontend, for both
│   │                              products — see frontend/ below)
│   ├── etl_real.py               ETL: raw exec-log/tracker exports -> real_history.csv
│   │                              + real_active_book.csv
│   ├── train_real.py             trains the production ensemble on real_history.csv
│   ├── build_book.py             rebuilds the scored book from sf_deals.csv + the two
│   │                              files above, backfilling anything Salesforce doesn't
│   │                              have coverage for yet
│   ├── data/                     real_history.csv, real_active_book.csv, sf_deals.csv,
│   │                              real_scored_book.csv (what real_mode serves) — NOT
│   │                              committed; see "Data" below
│   └── models/                   versioned model artifacts + registry.json — also not
│                                  committed
│
├── NewBusiness/backend/      New Business engine — same shape as backend/, deliberately
│   │                          isolated: its own ETL, own model, own data/models/
│   │                          directories, internal port 8011. Nothing here imports
│   │                          from backend/ or vice versa.
│   ├── app/
│   │   ├── nb_mode.py            serves the win-likelihood book: quotes, stages, rescoring
│   │   ├── insights_nb.py        likelihood bands, segment rollups, leaderboards
│   │   └── main.py               REST API (mounted by the gateway under /nb-app)
│   ├── etl_nb.py                 ETL: raw RSD export sheets -> the NB training set
│   ├── train_nb.py               trains the NB model
│   ├── build_book.py             rebuilds the scored open-pipeline book
│   ├── data/                     not committed, same reason as backend/data/
│   └── models/                   not committed
│
└── frontend/                 React + Vite + Recharts — ONE build serving BOTH
                                dashboards plus the product switcher (see App.jsx);
                                there is no separate New Business frontend to build
```

### The model (Renewals)

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

New Business follows the same validation discipline (`experiment_nb.py`) on its own,
separately-trained model — see `NewBusiness/backend/train_nb.py`.

## Data

This repo ships **code only**. `backend/data/`, `backend/models/`,
`NewBusiness/backend/data/`, and `NewBusiness/backend/models/` are all git-ignored, along
with every real client workbook this project reads (`.gitignore` denylists everything by
default and allowlists source code explicitly — see the comment at the top of that file).
None of it is in GitHub history either.

**Practical consequence: cloning this repo does not give you a runnable app.** There is no
demo mode and no fallback — `backend/app/main.py` fails loudly at startup if
`data/real_scored_book.csv` / `models/real_latest.joblib` are missing, same for the New
Business engine. To actually run it you need the raw Crumdale source exports (see
`etl_real.py` / `etl_nb.py` for the exact files each expects, and `DATA_REQUIREMENTS.md`
for what's needed to broaden coverage) and to run the ETL → train → build steps below
yourself. There's also nothing in the repo that identifies a particular person as "the"
user — see "Auth" below.

## Auth

Identity is resolved once, in `gateway/auth.py`, and nothing about it is hardcoded in the
repo:

* `HORIZON_AUTH_MODE=dev` (the default with no configuration) — no sign-in; you act as
  `HORIZON_DEV_USER` (default `dev@localhost`), and get the admin role by default
  (`HORIZON_DEV_ADMIN=0` to check the standard-user view locally instead).
* `HORIZON_AUTH_MODE=easyauth` (what the deployed app runs under) — trusts Microsoft Entra
  ID sign-in headers injected by Azure App Service Authentication in front of the process;
  a request with no verified header is rejected rather than treated as a guest.
  `HORIZON_ADMIN_EMAILS` (a comma-separated allowlist) decides who gets the admin role;
  an empty allowlist grants admin to no one. Both are Azure App Service settings, not
  anything checked into this repo.

So running this repo locally gives you a generic dev identity, not anyone's real one, and
deploying it doesn't carry any person's identity along — that's configured separately in
Azure per environment.

## Run it

```powershell
# 1. Renewals engine deps (once)
cd backend
python -m pip install -r requirements.txt

# 2. Build the real data + train (see backend/etl_real.py for the raw source files it expects)
python etl_real.py
python train_real.py
python build_book.py

# 3. New Business engine deps (once)
cd ..\NewBusiness\backend
python -m pip install -r requirements.txt

# 4. Build its data + train (see etl_nb.py for the raw source files it expects)
python etl_nb.py
python train_nb.py
python build_book.py

# 5. Frontend — ONE build serves both dashboards (once, or after any frontend change)
cd ..\..\frontend
npm install
npm run build

# 6. Serve everything through the gateway — the one process you actually run
cd ..\gateway
python -m pip install -r requirements.txt
python run_app.py
```

Startup fails loudly if either engine's scored book / model artifact isn't present —
there's no fallback demo mode, so a broken pipeline can't be masked by fake data. Run each
engine's three steps above first.

Open **http://localhost:8000** — the gateway serves the built dashboard for both products
and proxies API calls to whichever engine owns them.

For frontend development with hot reload: `npm run dev` in `frontend/` (proxies `/api` and
`/nb-app` to the gateway on port 8000, which must already be running the two engines).

`POST /api/retrain` (Renewals) and `POST /nb-app/api/retrain` (New Business) each run that
product's train + rebuild step and hot-swap the served state — this is what each
dashboard's "Monthly Retrain" / "Model Maintenance" controls call. Both are admin-only.

## API

Renewals (served unprefixed by the gateway):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/summary` | KPI rollup for the forecast window |
| `GET /api/forecast` | monthly + quarterly expected renewals / premium at risk |
| `GET /api/groups` | every group due in the window, scored, with reason codes |
| `GET /api/segments` | retention by LOB, RSD, AM, broker tier, tenure |
| `GET /api/data-quality` | training-data health report (admin) |
| `GET /api/renewal-database` | every historical renewal decision, row-level |
| `GET /api/model/metrics` | current metrics + full registry history (admin) |
| `GET /api/model/importance` | permutation feature importance (admin) |
| `GET /api/model/diagnostics` | calibration curve + ROC points (admin) |
| `POST /api/retrain` | monthly refresh: retrain, rebuild the book, rescore (admin) |
| `POST /api/groups/move-decided-to-database` | move every decided group into the Renewal Database (admin) |

New Business (same shape, under `/nb-app` — the gateway strips that prefix before
forwarding):

| Endpoint | Purpose |
| --- | --- |
| `GET /nb-app/api/summary` | KPI rollup for the open pipeline |
| `GET /nb-app/api/groups` | every open quote, scored, with a likelihood band |
| `POST /nb-app/api/groups/{quote_id}/stage` | move a quote's pipeline stage |
| `GET /nb-app/api/segments` | win rate by RSD, broker, product, industry, state |
| `GET /nb-app/api/performance` | leaderboards + year-over-year win-rate trend |
| `GET /nb-app/api/history` | every historical decided quote, row-level |
| `GET /nb-app/api/data-quality` | training-data health report (admin) |
| `GET /nb-app/api/model/metrics` | current metrics + registry history (admin) |
| `POST /nb-app/api/retrain` | retrain, rebuild the book, rescore (admin) |

Both products additionally expose `/api/admin/...` (and `/nb-app/api/admin/...`) endpoints
for bundle inspection/publishing — see `gateway/auth.py`'s `ADMIN_PREFIXES` for the full,
authoritative access-control list.
