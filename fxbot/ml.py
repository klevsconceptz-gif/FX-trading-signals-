"""Machine-learning forecasting: next-move direction & volatility.

Design notes (from the literature survey in RESOURCES.md):

* Gradient-boosted trees over rich technical features are the strongest
  practical baseline for FX direction prediction and beat naive LSTM
  setups on small/medium datasets (see michaeljf07/forex-predictor,
  Yildirim et al. 2021).  We use scikit-learn's GradientBoosting for
  portability (no TensorFlow/CUDA requirement).
* Training is strictly time-ordered (walk-forward style), never shuffled
  across time, so reported accuracy is honest out-of-sample.
* The model emits calibrated-ish probabilities via ``predict_proba``;
  the signal engine consumes ``P(up)`` as one factor among several.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

from .config import MLConfig
from .features import build_features, make_labels


@dataclass
class Forecast:
    """Model output for the most recent bar of an instrument."""

    symbol: str
    p_up: float                 # probability price is higher in `horizon` bars
    expected_move_pct: float    # predicted return magnitude, e.g. +0.0032
    vol_ratio: float            # predicted vol / current vol (dynamic stops)
    accuracy: Optional[float]   # out-of-sample accuracy of direction model
    trained_bars: int = 0
    feature_importance: dict[str, float] = field(default_factory=dict)

    @property
    def direction_score(self) -> float:
        """Map probability to a [-1, +1] factor for the signal engine."""
        return float(np.clip((self.p_up - 0.5) * 2.0, -1.0, 1.0))


class PairModel:
    """Direction classifier + return/volatility regressors for one symbol."""

    def __init__(self, cfg: Optional[MLConfig] = None):
        self.cfg = cfg or MLConfig()
        common = {
            "max_iter": self.cfg.n_estimators,
            "max_depth": self.cfg.max_depth,
            "learning_rate": self.cfg.learning_rate,
            "random_state": self.cfg.random_state,
            "early_stopping": True,
            "validation_fraction": 0.1,
        }
        self.clf = HistGradientBoostingClassifier(**common)
        self.ret_reg = HistGradientBoostingRegressor(**common)
        self.vol_reg = HistGradientBoostingRegressor(**common)
        self.accuracy_: Optional[float] = None
        self.feature_names_: list[str] = []
        self.feature_importance_: dict[str, float] = {}
        self.trained_bars_: int = 0

    # -- training ----------------------------------------------------------

    def _design(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        X = build_features(df)
        y = make_labels(df, self.cfg.horizon)
        return X, y

    def fit(self, df: pd.DataFrame) -> "PairModel":
        X, y = self._design(df)
        data = pd.concat([X, y], axis=1).dropna()
        if len(data) < self.cfg.min_train_bars:
            return self  # not enough data — stay untrained

        y_dir = data["direction"].astype(int)
        y_ret = data["fwd_ret"].astype(float)
        y_vol = data["fwd_vol"].astype(float)
        feats = data[X.columns]

        split = int(len(data) * (1.0 - self.cfg.test_fraction))
        # Labels at t use prices through t + horizon. Purge the final horizon
        # training rows so none of their outcomes overlap the held-out period.
        train_end = max(0, split - self.cfg.horizon)
        X_tr, X_te = feats.iloc[:train_end], feats.iloc[split:]
        d_tr, d_te = y_dir.iloc[:train_end], y_dir.iloc[split:]
        ret_tr, vol_tr = y_ret.iloc[:train_end], y_vol.iloc[:train_end]
        if len(X_tr) < 2 or d_tr.nunique() < 2:
            return self

        self.clf.fit(X_tr, d_tr)
        self.ret_reg.fit(X_tr, ret_tr)
        self.vol_reg.fit(X_tr, vol_tr)

        if len(X_te) > 20:
            self.accuracy_ = float((self.clf.predict(X_te) == d_te.to_numpy()).mean())
            imp_sample_X, imp_sample_y = X_te, d_te
        else:
            # Never substitute training accuracy for out-of-sample accuracy.
            self.accuracy_ = None
            imp_sample_X, imp_sample_y = X_tr, d_tr

        self.feature_names_ = list(X.columns)
        self.trained_bars_ = len(data)

        # one-shot permutation importance on a small tail sample (cheap)
        try:
            sample_X = imp_sample_X.tail(min(300, len(imp_sample_X)))
            sample_y = imp_sample_y.loc[sample_X.index]
            perm = permutation_importance(
                self.clf, sample_X, sample_y,
                n_repeats=3, random_state=self.cfg.random_state, n_jobs=1,
            )
            imp = perm.importances_mean
            top = np.argsort(imp)[::-1][:8]
            self.feature_importance_ = {
                self.feature_names_[i]: float(max(0.0, imp[i])) for i in top if imp[i] > 0
            }
        except Exception:
            self.feature_importance_ = {}
        return self

    # -- inference ---------------------------------------------------------

    def forecast(self, df: pd.DataFrame, symbol: str = "") -> Optional[Forecast]:
        X, _ = self._design(df)
        row = X.iloc[[-1]]
        if row.isna().any(axis=None):
            row = X.dropna().iloc[[-1]]
            if len(row) == 0:
                return None
        p_up = float(self.clf.predict_proba(row)[0][1])
        exp_move = float(self.ret_reg.predict(row)[0])
        vol_ratio = float(np.clip(self.vol_reg.predict(row)[0] / max(df["close"].pct_change().std(), 1e-9), 0.25, 4.0))
        return Forecast(
            symbol=symbol,
            p_up=p_up,
            expected_move_pct=exp_move,
            vol_ratio=vol_ratio,
            accuracy=self.accuracy_,
            trained_bars=self.trained_bars_,
            feature_importance=dict(self.feature_importance_),
        )

    # -- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(self, fh)

    @staticmethod
    def load(path: Path) -> Optional["PairModel"]:
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            return None


class MarketForecaster:
    """Trains / loads a model per symbol and produces forecasts for all."""

    def __init__(self, cfg: Optional[MLConfig] = None, model_dir: str = "models"):
        self.cfg = cfg or MLConfig()
        self.model_dir = Path(model_dir)
        self.models: dict[str, PairModel] = {}
        self.forecasts: dict[str, Forecast] = {}

    def run(self, history: dict[str, pd.DataFrame]) -> dict[str, Forecast]:
        self.forecasts = {}
        if not self.cfg.enabled:
            return self.forecasts
        for symbol, df in history.items():
            model = self.models.get(symbol)
            if model is None or model.trained_bars_ == 0:
                model = PairModel(self.cfg)
                model.fit(df)
                if model.trained_bars_ > 0:
                    self.models[symbol] = model
            if model.trained_bars_ > 0:
                fc = model.forecast(df, symbol)
                if fc is not None:
                    self.forecasts[symbol] = fc
        return self.forecasts
