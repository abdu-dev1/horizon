"""
Shared model-registry utilities.

The registry is a flat JSON append-log of every trained model version (real
retrains from train_real.py, plus this file's own append_registry/load_registry
helpers used by both real_mode.py and train_real.py) so predictive quality is
tracked release over release.
"""

from __future__ import annotations

import json
from pathlib import Path

from .paths import backend_dir

BACKEND_DIR = backend_dir()
MODELS_DIR = BACKEND_DIR / "models"
REGISTRY_PATH = MODELS_DIR / "registry.json"


def append_registry(entry: dict) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    registry.append(entry)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def load_registry() -> list:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return []
