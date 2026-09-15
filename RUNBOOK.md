# Horizon — Operations Runbook

Day-to-day operation of the app once it's deployed to Azure. Architecture and
reasoning live in [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md) — check its status
line first: as of this writing that deploy hasn't happened yet (Phase 6 is
unbuilt), so the Azure-specific sections below (Access, `az webapp` commands,
Deploying code) describe the target setup, not a live system. The monthly
refresh and packaging steps are accurate today regardless of hosting.

---

## The monthly refresh

Everything except the last step happens **on your laptop**. The deployed app
has no retrain button, by design: the ETL needs judgment. The September 2026
RSD export arrived silently filtered to `Effective Date >= 2026-01-01` — a
headless job would have rebuilt on 81 wins instead of 449 and reported success.

### 1. Drop the new source files in

| Product | Where | What |
|---|---|---|
| Renewals | `All-Data/` | the month's workbooks (BoB report, experience report, trackers) |
| New Business | `NewBusiness/` | `RSD Scorecard Export*.xlsx`, optionally `External Market Pricing*.xlsx` |

Do **not** delete the previous RSD export. `etl_nb.py` merges every export it
finds, oldest first, so a narrower newer pull corrects and adds rows without
being able to delete the history an older full pull carried.

### 2. Rebuild — three steps per product, not two

```bash
# Renewals
cd backend        && python etl_real.py && python train_real.py && python build_book.py

# New Business
cd NewBusiness/backend && python etl_nb.py && python train_nb.py && python build_book.py
```

`build_book.py` is the easy one to forget. Without it `nb_scored_book.csv` has
no `likelihood_band` / `expected_value` and the engine dies at startup with
`KeyError: 'expected_value'`.

### 3. Look at the numbers before publishing

```bash
cd backend             && python walk_forward.py
cd NewBusiness/backend && python experiment_nb.py
```

Check, at minimum:
- **Training row count** vs. last month. A large drop means a filtered or
  partial export, not a smaller book.
- **AUC** — flat or slightly up is normal; a sharp move in either direction
  wants explaining before it goes live.
- **Band/tier reliability** should stay monotonic (each band winning more often
  than the one below it).

### 4. Package

```bash
python publish.py            # both products, or: publish.py renewal | publish.py nb
```

Writes `dist_bundles/horizon-<product>-<version>.zip` and prints the version,
row counts and metrics it embedded. It refuses to package a product whose
required artifacts are missing.

### 5. Publish, in the app

Sign in as an admin, then per product:

| Product | Page |
|---|---|
| Renewals | Model Maintenance |
| New Business | Model Performance |

**Choose bundle…** validates and shows what it *would* install — including a
red warning if the bundle has >10% fewer training rows than what is live.
Then **Publish this model** promotes it. Two steps on purpose.

In-app edits survive a publish. NB stage edits and auto-expired rows are
reconciled back in; the renewal override CSVs are never in a bundle at all.

---

## Access

Sign-in is Entra ID, enforced by App Service before any request reaches the
app. There is no separate Horizon account.

**Granting or revoking admin:** edit the `HORIZON_ADMIN_EMAILS` app setting
(comma-separated). Changing it restarts the app — a few seconds of downtime.

```bash
az webapp config appsettings set -g <rg> -n horizon-app \
  --settings HORIZON_ADMIN_EMAILS="a@crumdalespecialty.com,b@crumdalespecialty.com"
```

An empty value grants **no one** admin. That is deliberate: reading "no admins
configured" as "everyone is an admin" would turn a forgotten setting into an
exposure.

Admins additionally see: Model Performance, Data Quality (both products) and
Model Maintenance. Standard users get the other 10 pages.

---

## Health and troubleshooting

`GET /healthz` is anonymous and checks **both** engines:

```json
{"status":"ok","engines":{"renewal":"up","new_business":"up"}}
```

`503` with an engine `unreachable` means that engine is down; the container
will be restarted by the platform.

```bash
az webapp log tail -g <rg> -n horizon-app          # live container logs
az webapp restart  -g <rg> -n horizon-app
```

| Symptom | Cause |
|---|---|
| Everything returns 401 | Easy Auth is not in front of the container. The app fails closed on purpose — a missing principal header means it is exposed, not that a guest arrived. |
| Dashboards blank, `/healthz` degraded | No data on the share yet. Publish a bundle for each product. |
| Container restart loop | Usually the health path became authenticated. `/healthz` must stay in Easy Auth's `excludedPaths`. |
| `KeyError: 'expected_value'` at startup | `build_book.py` was skipped in the rebuild. |
| Admin sees business pages only | Their email is not in `HORIZON_ADMIN_EMAILS` (match is case-insensitive, but whitespace/typos are not forgiven). |

---

## Things to know, not discover

- **One instance, no redundancy.** The app persists state by writing CSVs,
  which is not concurrency-safe, so it is pinned to a single instance. A
  restart or deploy is a short outage.
- **Retraining needs your laptop** and the raw workbooks. Nobody else can
  refresh the model. If that becomes a bottleneck, the fix is a second person
  set up to run the pipeline — not moving the ETL into Azure.
- **Raw client workbooks never leave the laptop.** They are not in the image,
  the repo, or the file share.
- **Deploying does not touch data.** The image is code only; data lives on the
  mounted shares and survives every deploy.

---

## Deploying code

There is no Docker image to build. Azure App Service's own builder (Oryx)
installs the root `requirements.txt` and runs `gateway/run_app.py` directly —
see [DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md)'s Phase 2 note for why an earlier
custom-image approach was dropped (a local `docker build` here filled this
dev machine's disk to zero). Phase 6 of that plan — the infrastructure that
would make an actual deploy possible — hasn't been written or run yet, so
there is no redeploy command to give here. Once it exists, this section
should hold it.

In the meantime, the standalone `Horizon.zip` build (`package_all.py`) is the
distribution path that already works, with no Azure dependency at all — see
that script's docstring for what it produces and how to send it.
