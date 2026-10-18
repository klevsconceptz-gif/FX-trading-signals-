"""ML forecaster tests: training, honest accuracy, forecast shape."""

import numpy as np
import pandas as pd
import pytest

import fxbot.ml as ml_module
from fxbot.config import MLConfig
from fxbot.features import build_features, make_labels
from fxbot.ml import MarketForecaster, PairModel


def test_feature_matrix_shape(eurusd):
    X = build_features(eurusd)
    assert len(X) == len(eurusd)
    assert "ret_1" in X.columns and "rsi14" in X.columns
    # no infinities
    assert not np.isinf(X.select_dtypes(include=[np.number]).to_numpy()).any()


def test_labels_are_forward_looking(eurusd):
    y = make_labels(eurusd, horizon=8)
    # last `horizon` rows must be NaN (unknown future)
    assert y["fwd_ret"].iloc[-8:].isna().all()
    assert y["fwd_ret"].notna().sum() > 100


def test_pair_model_trains_and_forecasts(eurusd):
    cfg = MLConfig(n_estimators=60, min_train_bars=300)
    model = PairModel(cfg).fit(eurusd)
    assert model.trained_bars_ > 300
    assert model.accuracy_ is not None
    assert 0.3 <= model.accuracy_ <= 0.95   # honest range on synthetic data

    fc = model.forecast(eurusd, "EURUSD")
    assert fc is not None
    assert 0.0 <= fc.p_up <= 1.0
    assert -1.0 <= fc.direction_score <= 1.0
    assert np.isfinite(fc.expected_move_pct)
    assert 0.25 <= fc.vol_ratio <= 4.0
    assert fc.feature_importance


def test_training_purges_forward_labels_at_time_split(monkeypatch):
    cfg = MLConfig(horizon=8, test_fraction=0.2, min_train_bars=10)
    model = PairModel(cfg)
    idx = pd.date_range("2025-01-01", periods=100, freq="h")
    X = pd.DataFrame({"feature": np.arange(100, dtype=float)}, index=idx)
    y = pd.DataFrame({
        "direction": np.arange(100) % 2,
        "fwd_ret": np.arange(100, dtype=float),
        "fwd_vol": np.arange(100, dtype=float),
    }, index=idx)

    class CaptureEstimator:
        def fit(self, features, labels):
            self.feature_index = features.index.copy()
            self.label_index = labels.index.copy()

    model._design = lambda _df: (X, y)
    model.clf = CaptureEstimator()
    model.ret_reg = CaptureEstimator()
    model.vol_reg = CaptureEstimator()
    monkeypatch.setattr(
        ml_module, "permutation_importance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("skip")),
    )

    model.fit(pd.DataFrame())

    split = 80
    assert len(model.clf.feature_index) == split - cfg.horizon
    assert model.clf.feature_index[-1] == idx[split - cfg.horizon - 1]
    assert model.clf.feature_index[-1] < idx[split]
    assert model.ret_reg.label_index.equals(model.clf.feature_index)
    assert model.accuracy_ is None  # no in-sample accuracy when holdout is small


def test_market_forecaster_full_universe(full_history, cfg):
    cfg_ml = MLConfig(n_estimators=40, min_train_bars=300)
    f = MarketForecaster(cfg_ml)
    out = f.run(dict(list(full_history.items())[:6]))
    assert len(out) >= 4
    for fc in out.values():
        assert 0 <= fc.p_up <= 1


def test_model_persistence_roundtrip(eurusd, tmp_path):
    cfg = MLConfig(n_estimators=40, min_train_bars=300)
    model = PairModel(cfg).fit(eurusd)
    path = tmp_path / "m.pkl"
    model.save(path)
    loaded = PairModel.load(path)
    assert loaded is not None
    a = model.forecast(eurusd, "X")
    b = loaded.forecast(eurusd, "X")
    assert a.p_up == pytest.approx(b.p_up)
