"""
Package locally-built artifacts into publishable bundles.

    python publish.py            both products
    python publish.py renewal    just the renewal bundle
    python publish.py nb         just the New Business bundle

Run this AFTER the pipeline and AFTER you have looked at the numbers:

    renewal:  python etl_real.py && python train_real.py && python build_book.py
    new biz:  python etl_nb.py   && python train_nb.py   && python build_book.py
    review:   python walk_forward.py   /   python experiment_nb.py

Output: dist_bundles/horizon-<product>-<version>.zip, which an admin uploads on
that product's Admin page in the deployed app.

Why publishing is a separate step from training
-----------------------------------------------
Because the review in the middle is the point. The deployed app has no retrain
button by design -- the ETL needs human judgment, and this session's own
history is the argument: the September 2026 RSD export arrived silently
filtered to Effective Date >= 2026-01-01, and rebuilding on it without looking
would have cut training history from 449 wins to 81 without raising an error.
An automated pipeline would have shipped that. A person looking at a row count
would not.

So this script refuses to package artifacts it cannot sanity-check, and prints
what it is about to publish so the numbers are in front of you one more time
before anything goes live.

What is deliberately NOT in a bundle
------------------------------------
Locally-owned files -- the override/registry CSVs on the renewal side, which
hold hand-made corrections that exist only on the server. See
backend/app/bundle.py's DELTA_FILES. Including one would wipe real work.
The raw client workbooks are not in a bundle either; they never leave the
laptop.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "dist_bundles"

RENEWAL = ROOT / "backend"
NB = ROOT / "NewBusiness" / "backend"

# (member path inside the zip, source file on disk, required?)
SPECS: dict[str, dict] = {
    "renewal": {
        "root": RENEWAL,
        "members": [
            ("data/real_history.csv", "data/real_history.csv", True),
            ("data/real_active_book.csv", "data/real_active_book.csv", False),
            ("data/real_scored_book.csv", "data/real_scored_book.csv", True),
            ("data/term_reasons.csv", "data/term_reasons.csv", False),
            ("data/sf_deals.csv", "data/sf_deals.csv", False),
            ("models/real_latest.joblib", "models/real_latest.joblib", True),
            ("models/registry.json", "models/registry.json", True),
        ],
        "history": "data/real_history.csv",
    },
    "nb": {
        "root": NB,
        "members": [
            ("data/nb_history.csv", "data/nb_history.csv", True),
            ("data/nb_pipeline.csv", "data/nb_pipeline.csv", True),
            ("data/nb_scored_book.csv", "data/nb_scored_book.csv", True),
            ("models/nb_latest.joblib", "models/nb_latest.joblib", True),
            ("models/registry.json", "models/registry.json", True),
        ],
        "history": "data/nb_history.csv",
    },
}

PRODUCT_NAME = {"renewal": "renewal", "nb": "new_business"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _registry_tail(root: Path) -> dict:
    """Latest entry from the model registry -- the AUC and band/tier
    reliability that were measured at training time. Carried into the manifest
    so the Admin view can show what it is about to promote, and so a published
    bundle is self-describing rather than needing the training log."""
    reg = root / "models" / "registry.json"
    if not reg.exists():
        return {}
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data if isinstance(data, list) else data.get("versions", data.get("entries"))
    if isinstance(entries, dict):
        entries = list(entries.values())
    return entries[-1] if entries else {}


# The two projects' registries store metrics differently: New Business nests
# them under a "metrics" key, the renewal side writes them flat on the entry
# (auc, brier, cv_auc_mean, ...). Normalising here rather than in either
# project keeps the manifest one shape for the Admin view, and stops a bundle
# publishing with a blank metrics block just because of a schema difference --
# which is exactly what happened on the first run of this script.
_FLAT_METRIC_KEYS = (
    "auc", "roc_auc", "pr_auc", "accuracy", "balanced_accuracy", "brier",
    "precision", "recall", "f1", "cv_auc_mean", "cv_auc_std",
    "n", "n_won", "n_train", "n_test", "base_rate", "base_renewal_rate",
    "naive_baseline", "auc_rich_subset",
)


def _metrics(latest: dict) -> dict:
    nested = latest.get("metrics")
    if isinstance(nested, dict) and nested:
        return nested
    return {k: latest[k] for k in _FLAT_METRIC_KEYS if k in latest}


def _row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def build(product: str) -> Path:
    spec = SPECS[product]
    root: Path = spec["root"]

    resolved: list[tuple[str, Path]] = []
    missing: list[str] = []
    for member, rel, required in spec["members"]:
        src = root / rel
        if src.exists():
            resolved.append((member, src))
        elif required:
            missing.append(rel)
    if missing:
        # Refuse rather than publish a partial bundle: the most likely cause is
        # a pipeline step that was skipped, and build_book.py is the easy one
        # to forget (without it nb_scored_book.csv has no likelihood_band or
        # expected_value, and the engine dies at startup on KeyError).
        raise SystemExit(
            f"cannot publish '{product}': missing {', '.join(missing)}\n"
            f"  run the full pipeline first -- etl -> train -> build_book -- "
            f"in {root}")

    latest = _registry_tail(root)
    version = str(latest.get("version") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    history_rows = _row_count(root / spec["history"])

    manifest = {
        "product": PRODUCT_NAME[product],
        "version": version,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": latest.get("version"),
        "trained_at": latest.get("trained_at"),
        "metrics": _metrics(latest),
        "band_reliability": latest.get("band_reliability") or latest.get("tier_reliability"),
        "history_rows": history_rows,
        "files": {member: {"sha256": _sha256(src), "bytes": src.stat().st_size}
                  for member, src in resolved},
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"horizon-{product}-{version}.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2, default=str))
        for member, src in resolved:
            zf.write(src, arcname=member)

    print(f"\n== {PRODUCT_NAME[product]} ==")
    print(f"  version        {version}")
    print(f"  trained at     {latest.get('trained_at') or 'unknown'}")
    print(f"  history rows   {history_rows}")
    metrics = _metrics(latest)
    if metrics:
        shown = {k: v for k, v in metrics.items()
                 if k in ("roc_auc", "auc", "pr_auc", "brier", "n", "n_won", "n_test", "base_rate")}
        print(f"  metrics        {json.dumps(shown, default=str)}")
    else:
        print("  metrics        NONE IN REGISTRY -- check the training run")
    print(f"  files          {len(resolved)}")
    print(f"  -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> None:
    wanted = sys.argv[1:] or list(SPECS)
    unknown = [w for w in wanted if w not in SPECS]
    if unknown:
        raise SystemExit(f"unknown product(s): {', '.join(unknown)}\n"
                         f"  choose from: {', '.join(SPECS)}")
    for product in wanted:
        build(product)
    print("\nReview the numbers above, then upload each bundle on that "
          "product's Admin page in the deployed app.")


if __name__ == "__main__":
    main()
