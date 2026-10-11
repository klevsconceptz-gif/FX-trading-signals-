"""Market data layer.

Two sources of truth:

1. ``SyntheticMarket`` — a *coherent* entire-market simulator.  Every
   currency has a latent log-factor driven by shared risk / commodity
   factors plus an idiosyncratic mean-reverting component with
   regime-switching volatility.  Every pair's price is ``exp(L_base -
   L_quote)``, so crosses are automatically arbitrage-consistent and the
   currency-strength engine has a real signal to extract.

2. Live providers (yfinance hourly, Frankfurter / ECB daily).  Used only when
   every instrument can be served live; otherwise the synthetic engine is the
   deterministic fallback, so the pipeline works offline and online alike.

All series returned are pandas DataFrames with columns
``open, high, low, close`` and a ``DatetimeIndex`` named ``time``.
"""

from __future__ import annotations

import hashlib
import math
import time
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
# Live providers
#
# Rules (all enforced in MarketData):
#   * a provider is only used when its native bar size matches the configured
#     timeframe — daily rates are never upsampled into hourly bars;
#   * live data is all-or-nothing per refresh: either every instrument comes
#     from a live provider, or the whole market is synthetic.  Mixing real and
#     simulated series would corrupt the cross-sectional strength and the
#     correlation matrix;
#   * a provider that fails ``LIVE_BREAKER_FAILURES`` times in a row is paused
#     for ``LIVE_BREAKER_COOLDOWN`` seconds, so an offline host costs seconds,
#     not minutes;
#   * the reason for any fallback is recorded in ``source_label``.
# ---------------------------------------------------------------------------

LIVE_BREAKER_FAILURES = 3
LIVE_BREAKER_COOLDOWN = 600.0   # seconds
MIN_LIVE_BARS = 100


class ProviderError(RuntimeError):
    """Network / parse failure (counts toward the circuit breaker)."""


def _try_fetch_json(url: str, timeout: float):
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "fxbot/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _daily_frame(points: dict[str, float]) -> pd.DataFrame:
    """Daily fixings (one rate per day) -> OHLC frame (open = prior close)."""
    dates = sorted(points)
    close = pd.Series([points[d] for d in dates], index=pd.to_datetime(dates), dtype=float)
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close.rolling(2, min_periods=1).max(),
        "low": close.rolling(2, min_periods=1).min(),
        "close": close,
    })[OHLC_COLS]


class _Provider:
    name = ""

    def native_hours(self, cfg: DataConfig) -> float:
        raise NotImplementedError

    def unavailable_reason(self) -> Optional[str]:
        return None

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        """Frame on success, None when this provider does not cover ``ins``.
        Raises ProviderError on network / parse failure."""
        raise NotImplementedError


class FrankfurterProvider(_Provider):
    """ECB reference rates via the Frankfurter v2 API (free, no key, daily).
    Covers fiat FX only; metals and most exotics are not published."""

    name = "frankfurter"

    def native_hours(self, cfg: DataConfig) -> float:
        return 24.0

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        if ins.kind != "fx":
            return None
        end = pd.Timestamp.now("UTC").date()
        start = end - pd.Timedelta(days=1200)
        url = ("https://api.frankfurter.dev/v2/rates"
               f"?base={ins.base}&quotes={ins.quote}"
               f"&from={start.isoformat()}&to={end.isoformat()}")
        try:
            rows = _try_fetch_json(url, cfg.request_timeout)
        except Exception as exc:
            raise ProviderError(f"frankfurter: {type(exc).__name__}") from exc
        if not isinstance(rows, list):
            raise ProviderError("frankfurter: unexpected response shape")
        points = {r["date"]: float(r["rate"]) for r in rows
                  if r.get("base") == ins.base and r.get("quote") == ins.quote}
        return _daily_frame(points) if points else None


class YFinanceProvider(_Provider):
    """Yahoo Finance FX / metal spot (hourly intraday history; needs yfinance)."""

    name = "yfinance"

    def native_hours(self, cfg: DataConfig) -> float:
        return 1.0 if cfg.timeframe == "1h" else 24.0

    def unavailable_reason(self) -> Optional[str]:
        try:
            import yfinance  # noqa: F401
        except Exception:
            return "yfinance not installed (pip install -r requirements-live.txt)"
        return None

    def fetch(self, ins: Instrument, cfg: DataConfig) -> Optional[pd.DataFrame]:
        import yfinance as yf

        interval = "60m" if cfg.timeframe == "1h" else "1d"
        period = "730d" if cfg.timeframe == "1h" else "5y"
        try:
            raw = yf.download(f"{ins.base}{ins.quote}=X", period=period, interval=interval,
                              progress=False, auto_adjust=True, timeout=cfg.request_timeout)
        except Exception as exc:
            raise ProviderError(f"yfinance: {type(exc).__name__}") from exc
        if raw is None or len(raw) == 0:
            # yfinance reports network errors as an empty frame, not an exception
            raise ProviderError("yfinance: empty response")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return raw.rename(columns=str.lower)[OHLC_COLS].dropna().astype(float)


PROVIDERS: dict[str, type[_Provider]] = {
    YFinanceProvider.name: YFinanceProvider,
    FrankfurterProvider.name: FrankfurterProvider,
}


def _normalize_live(frame: pd.DataFrame, bars: int) -> Optional[pd.DataFrame]:
    """Clean a provider frame: UTC index, sorted, de-duplicated, last ``bars`` rows."""
    idx = pd.DatetimeIndex(frame.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = frame[OHLC_COLS].copy()
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index().tail(bars)
    out = out.dropna()
    return out if len(out) >= MIN_LIVE_BARS else None


# ---------------------------------------------------------------------------
# Unified facade
# ---------------------------------------------------------------------------

class MarketData:
    """Fetch (or synthesize) OHLC history for the whole universe."""

    def __init__(self, cfg: Optional[DataConfig] = None):
        self.cfg = cfg or DataConfig()
        self.synthetic = SyntheticMarket(self.cfg)
        self.source_label = "synthetic (correlated factor model)"
        self.live_notes: list[str] = []
        self._breaker: dict[str, dict[str, float]] = {}
        self._history: dict[str, pd.DataFrame] = {}

    # -- circuit breaker ------------------------------------------------------

    def _paused(self, name: str) -> bool:
        state = self._breaker.get(name)
        return bool(state) and time.time() < state.get("until", 0.0)

    def _record_failure(self, name: str, exc: Exception) -> None:
        state = self._breaker.setdefault(name, {"fails": 0.0, "until": 0.0})
        state["fails"] += 1
        self.live_notes.append(str(exc))           # ProviderError text already names the provider
        if state["fails"] >= LIVE_BREAKER_FAILURES:
            state["until"] = time.time() + LIVE_BREAKER_COOLDOWN
            state["fails"] = 0.0
            self.live_notes.append(f"{name} paused {int(LIVE_BREAKER_COOLDOWN)}s after repeated failures")

    def _record_success(self, name: str) -> None:
        self._breaker[name] = {"fails": 0.0, "until": 0.0}

    # -- live fetch -----------------------------------------------------------

    def _fetch_live(self, ins: Instrument) -> Optional[tuple[str, pd.DataFrame]]:
        target_hours = 1.0 if self.cfg.timeframe == "1h" else 24.0
        for name in self.cfg.provider_order:
            cls = PROVIDERS.get(name)
            if cls is None:
                continue
            provider = cls()
            if provider.native_hours(self.cfg) > target_hours:
                continue                     # e.g. daily rates for an hourly model
            reason = provider.unavailable_reason()
            if reason:
                if reason not in self.live_notes:
                    self.live_notes.append(f"{name}: {reason}")
                continue
            if self._paused(name):
                continue
            try:
                raw = provider.fetch(ins, self.cfg)
            except ProviderError as exc:
                self._record_failure(name, exc)
                continue
            except Exception as exc:          # provider bug: never break the scan
                self._record_failure(name, ProviderError(f"{name}: {type(exc).__name__}"))
                continue
            self._record_success(name)
            if raw is None:
                continue
            frame = _normalize_live(raw, self.cfg.bars)
            if frame is not None:
                return name, frame
        return None

    def _load_live(self, targets: list[Instrument]) -> tuple[Optional[dict[str, pd.DataFrame]], str]:
        frames: dict[str, pd.DataFrame] = {}
        used: set[str] = set()
        self.live_notes = []
        for ins in targets:
            got = self._fetch_live(ins)
            if got is None:
                # all-or-nothing: stop at the first gap instead of mixing sources
                why = "; ".join(dict.fromkeys(self.live_notes)) or "no provider returned data"
                return None, f"{ins.symbol} unavailable — {why}"
            frames[ins.symbol] = got[1]
            used.add(got[0])
        return frames, ",".join(sorted(used))

    # -- public API -----------------------------------------------------------

    def load(self, symbols: Optional[list[str]] = None,
             prefer_live: bool = False) -> dict[str, pd.DataFrame]:
        """Load full history for ``symbols`` (default: entire universe)."""
        targets = [ins for ins in UNIVERSE if not symbols or ins.symbol in symbols]
        out: Optional[dict[str, pd.DataFrame]] = None
        if prefer_live:
            out, detail = self._load_live(targets)
            if out is not None:
                self.source_label = (f"live ({detail}, {self.cfg.timeframe}, "
                                     f"{len(out)} symbols, "
                                     f"as of {pd.Timestamp.now('UTC'):%Y-%m-%d %H:%M} UTC)")
            else:
                self.source_label = f"synthetic (live unavailable: {detail})"
        if out is None:
            out = {ins.symbol: self.synthetic.ohlc(ins) for ins in targets}
            if not prefer_live:
                self.source_label = "synthetic (correlated factor model)"
        self._history = out
        return out

    @property
    def history(self) -> dict[str, pd.DataFrame]:
        if not self._history:
            self.load()
        return self._history
