"""Market data layer.

Two sources of truth:

1. ``SyntheticMarket`` — a *coherent* entire-market simulator.  Every
   currency has a latent log-factor driven by shared risk / commodity
   factors plus an idiosyncratic mean-reverting component with
   regime-switching volatility.  Every pair's price is ``exp(L_base -
   L_quote)``, so crosses are automatically arbitrage-consistent and the
   currency-strength engine has a real signal to extract.

2. Live HTTP providers (Frankfurter / ECB, exchangerate.host, yfinance).
   They are tried in order and the synthetic engine is the deterministic
   fallback, so the pipeline works offline and online identically.

All series returned are pandas DataFrames with columns
``open, high, low, close`` and a ``DatetimeIndex`` named ``time``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .config import DataConfig
from .universe import CURRENCIES, METALS, UNIVERSE, Instrument

OHLC_COLS = ["open", "high", "low", "close"]


# ---------------------------------------------------------------------------
# Synthetic correlated market
# ---------------------------------------------------------------------------

@dataclass
class _Regime:
    name: str
    vol_mult: float
    drift_mult: float


REGIMES = [
    _Regime("calm", 0.55, 0.4),
    _Regime("normal", 1.00, 1.0),
    _Regime("volatile", 1.85, 1.4),
    _Regime("crisis", 3.10, -0.6),
]


class SyntheticMarket:
    """Deterministic, correlated FX market generated from currency factors."""

    #: base yearly volatility of a single currency log-factor
    BASE_VOL = 0.055
    #: half-life (in bars) of the idiosyncratic mean-reverting component
    OU_HALF_LIFE = 320.0

    def __init__(self, cfg: Optional[DataConfig] = None):
        self.cfg = cfg or DataConfig()
        self._factor_cache: dict[int, pd.DataFrame] = {}

    # -- factor construction ------------------------------------------------

    def _bars_per_year(self) -> float:
        return 24.0 * 252.0 if self.cfg.timeframe == "1h" else 252.0

    def _timeline(self, n: int) -> pd.DatetimeIndex:
        step = "h" if self.cfg.timeframe == "1h" else "D"
        return pd.date_range(end=pd.Timestamp.now("UTC").floor("h"), periods=n, freq=step)

    def _unit_factors(self, n: int) -> pd.DataFrame:
        """Return per-currency log-factor levels (columns) for n bars.

        Seeded by ``cfg.synthetic_seed`` and cached — every call with the
        same n yields the identical market (reproducible backtests).
        """
        if n in self._factor_cache:
            return self._factor_cache[n]

        seed = self.cfg.synthetic_seed
        rng = np.random.default_rng(seed)
        bpy = self._bars_per_year()
        dt = 1.0 / bpy

        codes = sorted(set(CURRENCIES) | set(METALS))
        n_c = len(codes)
        idx = self._timeline(n)

        # shared market factors: global risk-on/off + commodity cycle
        risk = np.zeros(n)
        comm = np.zeros(n)
        idio = np.zeros((n, n_c))
        mom = np.zeros((n, n_c))       # trend-persistent factor increments
        levels = np.zeros((n, n_c))

        kappa = math.log(2.0) / self.OU_HALF_LIFE          # mean-reversion
        phi = math.exp(-kappa)                             # AR coefficient
        # trend-persistent increments: stationary sd ≈ 0.35 × innov sd, giving
        # multi-week swings with realistic (near-zero) short-horizon autocorr
        phi_m = 0.88
        mom_noise = 0.35 * math.sqrt(1.0 - phi_m ** 2)

        # regime chain (Markov)
        vol_mult = np.ones(n)
        drift_mult = np.ones(n)
        r = 1
        stay = 0.995 if self.cfg.timeframe == "1h" else 0.985
        for t in range(n):
            if t and rng.random() > stay:
                r = int(rng.integers(0, len(REGIMES)))
            vol_mult[t] = REGIMES[r].vol_mult
            drift_mult[t] = REGIMES[r].drift_mult

        # slow macro drifts (deterministic per currency from seed)
        drift_rng = np.random.default_rng(seed + 1)
        drifts = drift_rng.normal(0.0, 0.012, n_c)

        vol_scale = math.sqrt(dt)
        risk_vol = 0.055
        comm_vol = 0.045

        for t in range(1, n):
            vm = vol_mult[t] * vol_scale
            risk[t] = risk[t - 1] + rng.normal(0.0, risk_vol * vm) \
                + 0.02 * dt * drift_mult[t]
            comm[t] = 0.985 * comm[t - 1] + rng.normal(0.0, comm_vol * vm)
            for j, code in enumerate(codes):
                params = CURRENCIES.get(code) or METALS[code]
                idio[t, j] = phi * idio[t - 1, j] \
                    + rng.normal(0.0, params["vol"] * self.BASE_VOL * vm)
                # persistent increments → multi-week trends like real FX
                mom[t, j] = phi_m * mom[t - 1, j] \
                    + rng.normal(0.0, params["vol"] * self.BASE_VOL * mom_noise * vm)
                shock = (params["beta_risk"] * (risk[t] - risk[t - 1])
                         + params["beta_comm"] * (comm[t] - comm[t - 1])
                         + (idio[t, j] - idio[t - 1, j])
                         + mom[t, j])
                levels[t, j] = levels[t - 1, j] + drifts[j] * dt * drift_mult[t] + shock

        # de-mean so pairs hover near their anchors
        levels -= levels.mean(axis=0)

        df = pd.DataFrame(levels, index=idx, columns=codes)
        self._factor_cache[n] = df
        return df

    def factor_levels(self, n: int) -> pd.DataFrame:
        return self._unit_factors(n)

    # -- price construction -------------------------------------------------

    def _log_price(self, ins: Instrument, n: int) -> pd.Series:
        f = self._unit_factors(n)
        base, quote = ins.base, ins.quote

        def leg(code: str) -> pd.Series:
            if code in f.columns:
                return f[code]
            # USD-quoted metals & crypto: synthetic "currency" via USD factor
            if code in ("XAU", "XAG"):
                # gold/silver are stores of value: inverse-USD + own factor
                return f["USD"] * -0.3 + f[code]
            return pd.Series(0.0, index=f.index)

        # For metals the quote is USD and price is USD per oz -> base - USD.
        return np.log(ins.start_price) + (leg(base) - leg(quote))

    def ohlc(self, ins: Instrument, n: Optional[int] = None) -> pd.DataFrame:
        """Generate a full OHLC frame for an instrument."""
        n = n or self.cfg.bars
        rng = np.random.default_rng(
            self.cfg.synthetic_seed + int(hashlib.md5(ins.symbol.encode()).hexdigest()[:6], 16)
        )
        close = np.exp(self._log_price(ins, n))
        close = pd.Series(close.to_numpy(), index=close.index, name="close")

        # intra-bar path: open near previous close, high/low from wick noise
        noise = np.abs(rng.normal(0.0, 1.0, n))
        step = close.diff().abs().fillna(0.0)
        wick = (0.35 * noise + 0.25) * step + 1e-6 * close.to_numpy() * 0.15
        open_ = close.shift(1).fillna(close.iloc[0] * (1 - 1e-4)).to_numpy() \
            + rng.normal(0.0, 0.12, n) * step.to_numpy()
        close_a = close.to_numpy()
        high = np.maximum(open_, close_a) + wick * rng.uniform(0.4, 1.0, n)
        low = np.minimum(open_, close_a) - wick * rng.uniform(0.4, 1.0, n)

        frame = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close_a},
            index=close.index,
        )
        return frame.astype(float)

    def history(self, symbols: Optional[list[str]] = None, n: Optional[int] = None) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for ins in UNIVERSE:
            if symbols and ins.symbol not in symbols:
                continue
            out[ins.symbol] = self.ohlc(ins, n)
        return out


# ---------------------------------------------------------------------------
# Live providers (best-effort; the sandbox may be offline -> synthetic)
# ---------------------------------------------------------------------------

def _try_fetch_json(url: str, timeout: float):
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "fxbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class FrankfurterProvider:
    """ECB reference rates via frankfurter.app (free, no key, daily)."""

    name = "frankfurter"

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        if ins.kind != "fx":
            return None
        try:
            data = _try_fetch_json(
                f"https://api.frankfurter.app/{ins.base}..{ins.quote}"
                f"?amount=1&from={ins.base}&to={ins.quote}",
                cfg.request_timeout,
            )
            rates = data.get("rates") or {}
            if not rates:
                return None
            dates = sorted(rates)
            closes = [rates[d][ins.quote] for d in dates]
            idx = pd.to_datetime(dates)
            close = pd.Series(closes, index=idx, dtype=float)
            frame = pd.DataFrame(
                {
                    "open": close.shift(1).fillna(close.iloc[0]),
                    "close": close,
                    "high": close.rolling(2, min_periods=1).max(),
                    "low": close.rolling(2, min_periods=1).min(),
                }
            )[OHLC_COLS]
            return frame
        except Exception:
            return None


class ExchangeRateHostProvider:
    """exchangerate.host timeseries (free tier, no key)."""

    name = "exchangerate_host"

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        if ins.kind != "fx":
            return None
        try:
            end = pd.Timestamp.now("UTC").date()
            start = end - pd.Timedelta(days=max(365, cfg.bars))
            data = _try_fetch_json(
                f"https://api.exchangerate.host/timeseries"
                f"?start={start}&end={end}&base={ins.base}&symbols={ins.quote}",
                cfg.request_timeout,
            )
            rates = (data or {}).get("rates") or {}
            if not rates:
                return None
            dates = sorted(rates)
            close = pd.Series([rates[d][ins.quote] for d in dates],
                              index=pd.to_datetime(dates), dtype=float)
            frame = pd.DataFrame(
                {
                    "open": close.shift(1).fillna(close.iloc[0]),
                    "close": close,
                    "high": close.rolling(2, min_periods=1).max(),
                    "low": close.rolling(2, min_periods=1).min(),
                }
            )[OHLC_COLS]
            return frame
        except Exception:
            return None


class YFinanceProvider:
    """Yahoo Finance FX/metal CFDs (best-effort, needs yfinance + network)."""

    name = "yfinance"

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf  # optional dependency
        except Exception:
            return None
        try:
            yahoo_symbol = f"{ins.base}{ins.quote}=X" if ins.kind == "fx" else ins.symbol
            interval = "60m" if cfg.timeframe == "1h" else "1d"
            period = "730d" if cfg.timeframe == "1h" else "5y"
            raw = yf.download(yahoo_symbol, period=period, interval=interval,
                              progress=False, auto_adjust=True)
            if raw is None or len(raw) < 50:
                return None
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            frame = raw.rename(columns=str.lower)[["open", "high", "low", "close"]]
            return frame.dropna().astype(float)
        except Exception:
            return None


PROVIDERS = {
    FrankfurterProvider.name: FrankfurterProvider,
    ExchangeRateHostProvider.name: ExchangeRateHostProvider,
    YFinanceProvider.name: YFinanceProvider,
}


# ---------------------------------------------------------------------------
# Unified facade
# ---------------------------------------------------------------------------

class MarketData:
    """Fetch (or synthesize) OHLC history for the whole universe."""

    def __init__(self, cfg: Optional[DataConfig] = None):
        self.cfg = cfg or DataConfig()
        self.synthetic = SyntheticMarket(self.cfg)
        self.source_label = "synthetic (correlated factor model)"
        self._history: dict[str, pd.DataFrame] = {}

    def _fetch_live(self, ins: Instrument) -> Optional[tuple[str, pd.DataFrame]]:
        for name in self.cfg.provider_order:
            provider = PROVIDERS.get(name)
            if provider is None:
                continue
            frame = provider().fetch(ins, self.cfg)
            if frame is not None and len(frame) > 100:
                # resample to requested timeframe if needed
                if self.cfg.timeframe == "1h" and frame.index.to_series().diff().median() > pd.Timedelta(hours=2):
                    frame = frame.resample("1h").ffill().dropna()
                return name, frame
        return None

    def load(self, symbols: Optional[list[str]] = None,
             prefer_live: bool = False) -> dict[str, pd.DataFrame]:
        """Load full history for ``symbols`` (default: entire universe)."""
        out: dict[str, pd.DataFrame] = {}
        live_hits = 0
        for ins in UNIVERSE:
            if symbols and ins.symbol not in symbols:
                continue
            frame = None
            if prefer_live:
                got = self._fetch_live(ins)
                if got:
                    self.source_label = f"live:{got[0]}"
                    frame = got[1]
                    live_hits += 1
            if frame is None:
                frame = self.synthetic.ohlc(ins)
            out[ins.symbol] = frame
        if not prefer_live or live_hits == 0:
            self.source_label = "synthetic (correlated factor model)"
        else:
            self.source_label = f"live ({live_hits}/{len(out)} symbols via {self.cfg.provider_order[0]})"
        self._history = out
        return out

    @property
    def history(self) -> dict[str, pd.DataFrame]:
        if not self._history:
            self.load()
        return self._history
