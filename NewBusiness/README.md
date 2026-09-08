# New Business — Win-Likelihood Forecasting

Predicts the likelihood that an open, first-time-client quote (not a renewal)
will close as a won sale. A sibling project to Horizon (the renewal
forecaster), deliberately kept separate.

## Isolation rule

**The only source of truth for this project is the "RSD Scorecard Export"
workbook that lives directly in this `NewBusiness/` folder.** The ETL
(`backend/etl_nb.py`) never reads from, and nothing in this project ever
writes into, the sibling renewal project (`ForecastEngine/backend`,
`ForecastEngine/All-Data`, etc.) — and vice versa. Different unit of analysis
(a quote, not a renewal), different label (won a first-time sale vs. renewed
an existing one, ~4.7% vs. ~60-70% base rate), different leakage rules. Do
not add a second data source to `etl_nb.py` without re-reading this note.

## Architecture

```
NewBusiness/
├── RSD Scorecard Export - *.xlsx     the one input file
├── backend/
│   ├── etl_nb.py            RSD Scorecard Export -> nb_history.csv / nb_pipeline.csv
│   ├── train_nb.py          calibrated RF+ET+HGB ensemble, honest 5-fold CV metrics
│   ├── build_book.py        scores the open pipeline, attaches the likelihood band
│   ├── experiment_nb.py     the honest eval harness — changes prove themselves HERE first
│   ├── app/
│   │   ├── nb_mode.py       serving layer: state, groups, segments
│   │   ├── insights_nb.py   likelihood bands (High/Moderate/Low/Very Low), reason
│   │   │                    codes, and the exec Sales Performance analytics
│   │   └── main.py          standalone FastAPI app (dev: port 8001)
│   ├── data/                nb_history.csv, nb_pipeline.csv, nb_scored_book.csv
│   └── models/              nb_latest.joblib, registry.json
```

In the combined Horizon product, this app is mounted by the top-level
gateway (`ForecastEngine/gateway/`) under its own path prefix, alongside the
renewal app — one URL, one port, for whoever's using the dashboard. Neither
app is modified to make that work; the gateway just mounts both, unchanged.

## Run standalone (dev)

```powershell
cd NewBusiness\backend
python -m pip install -r requirements.txt
python etl_nb.py
python train_nb.py
python build_book.py
python -m uvicorn app.main:app --port 8001
```

## Data notes

- `Sold` is 1 only for `Stage == "Closed Won"`; `Stage == "Closed Lost"` and
  every still-open stage both show `Sold == 0` — the decided/open split is by
  `Stage`, not `Sold` alone.
- `Notice of Sale Date` and `Loss Reason` are assigned only after the
  decision (Notice of Sale Date is populated in 98.7% of wins vs. 0.1% of
  losses) — excluded from the model entirely, kept only for the historical
  database view.
- `broker` (740 distinct values), `rsd`, `industry`, and `billing_state` are
  win-rate target-encoded (smoothed), not one-hot — a 740-way one-hot against
  446 total wins is pure overfitting bait.
- One sandbox/dummy row family is excluded outright in `etl_nb.py` (any
  standalone word "test" in the opportunity name, with `Hy-Test Safety Shoe
  Service` allowlisted back in).
- `industry` coalesces `Industry.1` then `Industry` (99.8% populated between
  them; either alone is 87-91%). `Industry.2` duplicates `Industry.1` and is
  unused.
- Decisions recorded **in the app** — a manual stage edit to Closed Won/Lost,
  or the past-effective-date auto-expire — are re-appended by `etl_nb.py` on
  every rebuild (`_preserve_local_decisions`) and removed from the open
  pipeline in the same pass. Before 2026-08-26 a rebuild silently discarded
  them, including the rebuild that `/api/retrain` runs.

## Serve consistency — why the headline metric dropped on 2026-08-26

The model is only ever asked about an **open** quote, so it may only use
fields an open quote actually has. It previously trained on 19 numerics
including the illustrative/firm quote costs, ISL deductible, UW turnaround
days and the assigned underwriter. Those are well populated on decided
history and essentially absent on the open pipeline:

| field | decided | open |
|---|---|---|
| `isl_deductible` | 67.4% | 0.0% |
| `has_illustrative_quote` | 74.9% | 4.4% |
| `days_created_to_illustrative` | 61.1% | 0.0% |
| `underwriter` | 41.9% | 0.0% |
| `firm_*`, `days_illustrative_*` | 13–21% | 0.0% |

So it reported ROC 0.944 / PR 0.505 while actually operating at **ROC 0.736 /
PR 0.203** on a real open quote, and — having learned "firm quote exists →
likely win" when no open quote has one — it ranked quotes *backwards* against
pipeline stage: every quote at a Firm Quote stage landed in the bottom band.

Training on serve-available fields only measures **ROC 0.804 / PR 0.255**:
lower than the old headline, higher than the old reality, and reachable in
production. `experiment_nb.py` holds the whole comparison, including four
rejected changes and one instructive near-miss:

| change | verdict |
|---|---|
| geographic target encoding (zip3 + city) | −0.065 ROC — rejected |
| renewal-vs-current cost ratio | no effect — rejected |
| `eff_month` target-encoded vs one-hot | no effect — kept one-hot |
| recency weighting (1y–3y half-life) | −0.006 ROC — rejected |
| `laser_count` | +0.020 PR on history CV, but constant 0 on every open quote — rejected (`round4_laser_trap`) |

Recency weighting deserves a note: the book's win rate is genuinely
non-stationary (11.1% in 2022 → 4.9% → 5.2% → 4.4% → 2.2% in 2026 to date), so
down-weighting old quotes *should* have helped. Best case it traded 0.006 ROC
overall for 0.006 ROC on the recent slice — inside two-seed noise. Worth
re-testing as the regimes diverge further.

**The rule this establishes:** a candidate feature has to earn its place under
masked evaluation, not on history CV. History CV is where the old mistake
looked like a good number.

**Highest-value data change available:** `Stage` is the most predictive field
an open quote carries and the model cannot use it, because on decided history
stage *is* the label and the export preserves no stage-at-decision-time. If
the export can carry a stage-history or stage-snapshot column, that unlocks
it. Until then the exec team's rule-of-thumb close rate per stage is shown
separately and never blended into the measured rate
(`insights_nb.STAGE_SALES_ESTIMATE`).

## Likelihood bands

`High / Moderate / Low / Very Low` (`insights_nb.BAND_ORDER`), replacing the
old `Hot / Warm / Long Shot / Cold` on 2026-08-26. Two reasons: "Hot"/"Cold"
describe a salesperson's enthusiasm rather than a likelihood, and the four old
labels implied four distinguishable levels when the middle two measured 15.8%
vs. 12.7% — statistically the same band wearing two names. Each band's
dashboard number is its **measured out-of-fold win rate**
(`band_reliability`), not a raw per-quote score.

## Sales Performance page

`/api/performance` (`insights_nb.performance`) — the decided book by year, RSD,
broker, product, industry, state, deal size and effective month. Pure measured
history, **no model output anywhere in it**, so it stays checkable by hand
against the export and never moves because a model was retrained. Both an
effective-year and a created-year basis are returned; years that still have
open quotes are flagged `in_progress` because their win rate can only rise.
Won-premium figures always ship with their coverage (only 80% of wins carry a
cost figure at all).
