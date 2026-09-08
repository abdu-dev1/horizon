"""
NewBusiness book builder — scores the open pipeline with the latest trained
model and attaches the likelihood band, producing the final data/nb_scored_book.csv
that the API serves.

Run:  python build_book.py   (from NewBusiness/backend/, after train_nb.py)
"""
from __future__ import annotations

import json

import train_nb
from app import insights_nb, paths

DATA = paths.backend_dir() / "data"
MODELS = paths.backend_dir() / "models"


def _latest_band_reliability() -> dict:
    registry = json.loads((MODELS / "registry.json").read_text())
    if not registry:
        raise RuntimeError("models/registry.json is empty — run train_nb.py first")
    latest = registry[-1]
    if "band_reliability" not in latest:
        raise RuntimeError(
            f"model version {latest.get('version')} has no band_reliability — "
            "it was trained before that field existed; retrain with train_nb.py."
        )
    return latest["band_reliability"]


def build() -> None:
    pipeline = train_nb.score_pipeline(verbose=False)
    band_reliability = _latest_band_reliability()
    scored = insights_nb.score_book(pipeline, band_reliability)
    scored.to_csv(DATA / "nb_scored_book.csv", index=False)
    print(f"scored + banded {len(scored)} open quotes -> {DATA / 'nb_scored_book.csv'}")
    print(scored["likelihood_band"].value_counts().to_string())


if __name__ == "__main__":
    build()
