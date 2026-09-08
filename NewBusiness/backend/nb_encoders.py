"""
Custom sklearn-compatible transformers for the NewBusiness model.

Kept in their OWN module (not inline in train_nb.py) deliberately: a class
defined in a script that gets run directly (`python train_nb.py`) is pickled
as belonging to `__main__` for THAT run, which breaks when a different entry
point (build_book.py, app/nb_mode.py) tries to unpickle the saved model and
has no such class in ITS OWN `__main__`. Living here, the class's real
importable path (`nb_encoders.SmoothedTargetEncoder`) is stable no matter
which script loads the model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class SmoothedTargetEncoder(BaseEstimator, TransformerMixin):
    """Per-category smoothed mean-target (win-rate) encoding.

    Fit inside a Pipeline/ColumnTransformer, so cross_val_predict refits it on
    each training fold only -- the CV metrics can never leak through this step.
    Unseen categories (or ones with few observations) fall back toward the
    overall win rate, weighted by `smoothing` "pseudo-observations" -- a broker
    with 1 quote and a fluke win should read close to the base rate, not 100%.
    """

    def __init__(self, columns: list[str], smoothing: float = 10.0):
        self.columns = columns
        self.smoothing = smoothing

    def fit(self, X: pd.DataFrame, y):
        y = np.asarray(y, dtype=float)
        self.global_mean_ = float(y.mean())
        self.maps_ = {}
        for col in self.columns:
            stats = pd.DataFrame({"cat": X[col].values, "y": y}).groupby("cat")["y"].agg(["mean", "count"])
            smoothed = (stats["mean"] * stats["count"] + self.global_mean_ * self.smoothing) / (
                stats["count"] + self.smoothing)
            self.maps_[col] = smoothed.to_dict()
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        out = np.empty((len(X), len(self.columns)), dtype=float)
        for i, col in enumerate(self.columns):
            out[:, i] = X[col].map(self.maps_[col]).fillna(self.global_mean_).to_numpy()
        return out

    def get_feature_names_out(self, input_features=None):
        return np.array([f"{c}_win_rate" for c in self.columns])
