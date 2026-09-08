# Horizon — Azure Deployment Plan

Status: **planning** (nothing in phases 1-7 started). Written 2026-09-08.

Target: the combined Horizon app (Renewals + New Business) running on Azure App
Service behind Entra ID SSO, serving a **published, versioned data+model bundle**
that is built on an admin's laptop — never in the cloud.

---

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Hosting | One Linux container, all 3 processes | Mirrors dev exactly; the two backends both ship a package named `app` and can never share a process |
| Docker | Yes | Three processes from one entrypoint, and `scikit-learn` must be pinned to **1.3.2** or the pickled model breaks |
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

Also delete (confirm first — they look like stale session artifacts):
`ALL_PHASES_COMPLETE.md`, `FINAL_FIX_SUMMARY.md`, `IMPLEMENTATION_SUMMARY.md`.

**Keep** `walk_forward.py`, `experiment.py`, `experiment_nb.py` — these are the
honest-evaluation gate and get *more* important once retraining is offline.

## Phase 2 — Config + containerize

- Pin all three `requirements.txt` exactly: `scikit-learn==1.3.2`, `numpy==1.26.2`,
  `pandas==2.1.3`, `scipy==1.16.3`, `joblib==1.3.2`, `fastapi==0.104.1`,
  plus `uvicorn`, `openpyxl`, `httpx`, `python-multipart`.
- Env-var config to replace hardcoded values:
  `gateway/main.py:31-32` (`RENEWAL_BASE`/`NB_BASE`), bind `0.0.0.0` not `127.0.0.1`.
- `Dockerfile`: node stage builds `frontend/dist` → `python:3.12-slim` runtime,
  installs all three requirement sets, entrypoint launches all three processes.
- `.dockerignore` (mirror the `.gitignore` allowlist logic).
- Add `GET /healthz` on the gateway for Azure's probe.
- **Gate:** `docker run` locally, all endpoints answer through the container.

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

- Resource group; Azure Container Registry; App Service Plan **B2 or P1v3**
  (the NB model alone is 148 MB resident — not a free/shared tier).
- App Service (Linux container), **scale pinned to 1 instance** (the CSV writes
  are not concurrency-safe).
- Azure Files share mounted at the delta-store path; Blob container for bundles.
- App settings: env vars from Phase 2 + `HORIZON_ADMIN_EMAILS` + storage
  connection (via Key Vault reference, not a literal).
- **Gate:** deployed URL serves both dashboards through the gateway.

## Phase 7 — Verify + document

Verify on the deployed URL, not locally:
- anonymous blocked; admin vs non-admin nav correct
- both dashboards' key endpoints return 200
- an NB stage edit survives a container restart
- served model version + AUC match the published `MANIFEST.json`
- container restart time (this is the outage window — single instance, no redundancy)

Then write a monthly runbook: drop new workbooks → run the 6 scripts → review AUC →
`publish.py` → click Reload in the Admin view.

---

## Known accepted trade-offs

- **Single instance** = a container restart is a short outage. Fine for an internal
  tool; you should know it rather than discover it.
- **Retrain requires the admin's laptop** + the raw workbooks. Deliberate, per above.
- **Admin allowlist is a second place to maintain** and will drift when someone
  leaves; an Entra group is the upgrade path.
- **The three `.spec` files contain absolute local paths** (`C:\Users\...`) and are
  already pushed to GitHub. Moot once Phase 1 deletes them.
