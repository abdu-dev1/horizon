# Horizon — Azure Deployment Plan

Status as of 2026-09-14: **phases 1-5 built and verified locally. Phase 2's
original approach — a custom Docker image built via `az acr build` — was
dropped in favor of a direct Oryx deploy**: App Service's own builder installs
the root `requirements.txt` and runs `gateway/run_app.py` directly, so there
is no image to build at all (see the note under Phase 2). **Phase 6 needs to
be rewritten for that approach and has not been executed** (still no Azure
CLI on the dev machine either, so no Azure resources exist). Phase 7 is
documented in [RUNBOOK.md](RUNBOOK.md); its verification steps run against a
deployed URL and are therefore still outstanding.

In the meantime, `package_all.py` builds a separate, already-working
distribution path that has no Azure dependency at all: a standalone Windows
build (`Horizon.zip`) a recipient runs locally. See that script's docstring.

| Phase | State |
|---|---|
| 1 Strip the desktop path | done -- 1,808 lines removed |
| 2 Deploy path | pivoted from Docker/ACR to a direct Oryx build off the root `requirements.txt` -- no image to validate |
| 3 Auth + authorization | done, verified against a running app |
| 4 Role split | done, verified |
| 5 Publish/bundle pipeline | done, round trip verified incl. edit preservation |
| 6 Azure infrastructure | not started for the Oryx approach -- the Docker-era `infra/main.bicep` + `infra/deploy.ps1` were removed and nothing has replaced them |
| 7 Verify on the real URL | outstanding -- needs phase 6 |

Target: the combined Horizon app (Renewals + New Business) running on Azure App
Service behind Entra ID SSO, serving a **published, versioned data+model bundle**
that is built on an admin's laptop — never in the cloud.

---

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Hosting | One Linux App Service, all 3 processes | Mirrors dev exactly; the two backends both ship a package named `app` and can never share a process |
| Custom Docker image | No -- dropped | A local `docker build` fills this dev machine's disk to zero (see Phase 2); Oryx needs no image at all, just `requirements.txt` |
| Dependency pins | Exact versions, not floors, in `requirements.txt` | `scikit-learn` must match **1.3.2** or the pickled models break -- see the comment at the top of that file |
| Auth | Entra ID SSO via App Service Easy Auth | Platform-level, no auth code in FastAPI |
| Admin role | Hardcoded email allowlist (app setting) | Chosen for simplicity; migrate to an Entra group later if it drifts |
| Writable state | Azure Files mount, single instance | Keeps every existing CSV write working with zero code change |
| Desktop `.exe` | **Dropped** — web only | Removes ~1,208 lines + 24 frozen-mode branches |
| Retrain | **Local only**, on the admin's laptop | The ETL needs human judgment (see below) |

### Why the ETL never runs in Azure

The September 2026 RSD export was silently filtered to `Effective Date >= 2026-01-01`.
Rebuilding on it blindly would have cut training history from 449 wins to 81 — with
no error raised. A human had to notice and choose to *merge* rather than *swap*.
The same applies to the loss-ratio maturity gate, the manual override CSVs, and
every blank-vs-fake decision. Automating that is automating the judgment away.

**Consequence:** Azure is a read-mostly serving layer. The laptop is the build box.

---

## The bundle / delta split (the core architectural idea)

A retrain produces both a model *and* the derived CSVs the app serves. Those travel
together as one immutable, versioned **bundle**:

```
bundle/<version>/
  models/real_latest.joblib, registry.json
  models/nb_latest.joblib,   registry.json
  data/real_scored_book.csv, real_history.csv, real_active_book.csv, ...
  data/nb_history.csv, nb_pipeline.csv, nb_scored_book.csv
  MANIFEST.json   version, row counts, AUC, band reliability, source files, sha256
```

But the app also **writes**: NB stage edits, outcome overrides, auto-expire, uploads.
Those happen in Azure, where the laptop can't see them. So they are NOT stored in the
bundle — they live in a separate **delta store** on the Azure Files mount:

```
delta/
  nb_stage_edits.csv, nb_auto_expired.csv
  outcome_overrides.csv, excluded_groups.csv, lob_overrides.csv
```

On load: **published bundle (immutable) + delta layer re-applied on top.**
A publish therefore never destroys an edit someone made in the browser.
This generalises what `etl_nb._preserve_local_decisions` already does on one machine.

---

## Phase 1 — Strip the desktop path

Do this first: everything afterwards gets simpler.

- Delete `gateway/run_app_frozen.py` (361), `package_all.py` (138),
  `backend/package_app.py`, `NewBusiness/backend/package_app.py`,
  all three `build/*.spec`, `dist_combined/`, `*/dist/`, `*/build/`, `*/_seed_stage/`.
- Collapse both `app/paths.py`: remove `_frozen()`, `bundle_dir()`, `_MEIPASS`
  handling, `ensure_seeded()`, `_seed_version()`. Replace with one env-var-driven
  data/model root (`HORIZON_DATA_DIR`, `HORIZON_MODEL_DIR`), defaulting to the
  repo-relative dev paths.
- Remove the 24 frozen-mode branches across the two backends.
- Simplify `backend/run_app.py` and `NewBusiness/backend/run_app.py` (drop frozen
  dispatch; keep the `HORIZON_PORT` env override).
- **Gate:** app still starts and all pages load locally after this.

*(Note: every file this bullet removed was later reintroduced, unchanged in
purpose but rewritten, for the standalone-exe packaging path described under
"Known accepted trade-offs" below — that path did not exist yet when this
phase ran, and is unrelated to the desktop `.exe` mode being stripped here.)*

Also delete (confirm first — they look like stale session artifacts):
`ALL_PHASES_COMPLETE.md`, `FINAL_FIX_SUMMARY.md`, `IMPLEMENTATION_SUMMARY.md`.

**Keep** `walk_forward.py`, `experiment.py`, `experiment_nb.py` — these are the
honest-evaluation gate and get *more* important once retraining is offline.

## Phase 2 — Config + deploy path

- Pin all three `requirements.txt` exactly: `scikit-learn==1.3.2`, `numpy==1.26.2`,
  `pandas==2.1.3`, `scipy==1.16.3`, `joblib==1.3.2`, `fastapi==0.104.1`,
  plus `uvicorn`, `openpyxl`, `httpx`, `python-multipart`. Union them into the
  root `requirements.txt` -- the one file Oryx (App Service's Python builder)
  reads, since it only looks at the repo root and this app has three
  requirement sets (see the comment at the top of that file).
- Env-var config to replace hardcoded values:
  `gateway/main.py:31-32` (`RENEWAL_BASE`/`NB_BASE`), bind `0.0.0.0` not `127.0.0.1`.
- Add `GET /healthz` on the gateway for Azure's probe; exclude it from Easy
  Auth or the probe fails and Azure restarts the app in a loop.
- **Dropped deliberately: a custom Docker image.** The original plan built one
  via `az acr build` server-side (a local `docker build` here was never an
  option -- see below), but committing the built `frontend/dist` and letting
  Oryx install `requirements.txt` directly needs no image, no registry, and no
  build step of any kind, so the whole failure mode below goes away rather
  than just moving server-side.
- **Gate:** the app starts under a plain `python gateway/run_app.py` with only
  the root `requirements.txt` installed -- the same thing Oryx does.

*Why not build the old Docker image locally, even as a pre-check:* it peaks
around 6-8 GB (two base images, the sklearn/scipy/pandas install,
`node_modules`, the ~1.5 GB result, plus BuildKit caching every intermediate
layer) against ~2 GB free on this machine. An attempt on 2026-09-08 filled the
disk to 0 bytes and wedged Docker Desktop, which then could not start to prune
its own cache. That machine constraint is what pushed this phase toward a
build mechanism that needs no image at all.

## Phase 3 — Security

- Enable Easy Auth (Entra ID) on the App Service; exclude `/healthz` or the
  probe fails and Azure restarts the container in a loop.
- Read the signed-in user from the `X-MS-CLIENT-PRINCIPAL-NAME` header injected
  by Easy Auth. Never trust a client-supplied email.
- Admin check against `HORIZON_ADMIN_EMAILS` app setting (comma-separated).
- Replace `allow_origins=["*"]` in both backends — same-origin once the gateway
  serves the SPA, so CORS can mostly go away.
- **Gate:** anonymous request returns 401/redirect; verified, not assumed.

## Phase 4 — Admin view + role split

- `GET /api/me` → `{ email, is_admin }`.
- Move these 5 operator pages behind `is_admin` (business users go 15 → 10 pages):
  Model Performance (renewal + NB), Data Quality (renewal + NB), Model Maintenance.
- Admin view shows: live bundle version, both models' AUC + band reliability +
  trained date, delta-store contents, publish/reload controls.
- **Gate:** a non-admin account cannot see or call the admin endpoints.

## Phase 5 — Retrain + publish pipeline

Local, unchanged, human-in-the-loop:
```
etl_real.py   → train_real.py → build_book.py      (renewal)
etl_nb.py     → train_nb.py   → build_book.py      (new business)
walk_forward.py / experiment*.py                   (review the numbers)
```
Then:
- New `publish.py`: validates the built artifacts (row counts sane, model loads,
  AUC present), writes `MANIFEST.json`, uploads the bundle to Blob Storage under a
  new version.
- App loads the current bundle at startup; `POST /api/admin/reload` swaps to the
  newest without a redeploy.
- Implement the **delta layer** (see above) so publishing preserves in-app edits.
- **Delete both synchronous `/api/retrain` endpoints** — they cannot work in Azure
  (4-minute run vs. a 230-second request timeout) and they are what the local
  pipeline replaces.
- **Gate:** publish a bundle; a stage edit made in the browser beforehand survives it.

## Phase 6 — Azure infrastructure

**Not started.** The Bicep template and `deploy.ps1` written for the
Docker/ACR approach were removed along with the Dockerfile (they provisioned
a container registry and pointed the App Service at an image, neither of
which the Oryx approach needs), and nothing has replaced them yet. Still
needed, once someone picks this back up:

- Resource group; App Service Plan **B2 or P1v3**, Linux, Python runtime
  (the NB model alone is 148 MB resident — not a free/shared tier).
- App Service, **scale pinned to 1 instance** (the CSV writes are not
  concurrency-safe).
- Azure Files share mounted at the delta-store path; Blob container for bundles.
- App settings: env vars from Phase 2 + `HORIZON_AUTH_MODE=easyauth` +
  `HORIZON_ADMIN_EMAILS` + storage connection (via Key Vault reference, not a
  literal).
- Easy Auth (Entra ID) enabled on the App Service, `/healthz` excluded from it.
- **Gate:** deployed URL serves both dashboards through the gateway.

## Phase 7 — Verify + document

Verify on the deployed URL, not locally:
- anonymous blocked; admin vs non-admin nav correct
- both dashboards' key endpoints return 200
- an NB stage edit survives a container restart
- served model version + AUC match the published `MANIFEST.json`
- container restart time (this is the outage window — single instance, no redundancy)

The monthly operating procedure this implies — drop new workbooks → run the 6
scripts → review AUC → `publish.py` → click Reload in the Admin view — is
already written up in [RUNBOOK.md](RUNBOOK.md).

---

## Known accepted trade-offs

- **Single instance** = a container restart is a short outage. Fine for an internal
  tool; you should know it rather than discover it.
- **Retrain requires the admin's laptop** + the raw workbooks. Deliberate, per above.
- **Admin allowlist is a second place to maintain** and will drift when someone
  leaves; an Entra group is the upgrade path.
- **The three `.spec` files** drive the separate PyInstaller packaging path
  (`package_all.py`, added after this plan's Phase 1); they resolve every
  path off PyInstaller's own `SPECPATH` rather than a hardcoded machine path,
  so they build unmodified for whoever runs them next.
