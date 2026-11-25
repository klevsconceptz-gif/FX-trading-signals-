"""Tradable universe: FX pairs, metals and FX-adjacent assets.

The same module powers the synthetic factor model, the live-data adapters
and the scanner, so adding an instrument here extends the entire platform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str            # canonical symbol, e.g. "EURUSD"
    base: str              # base currency (or metal code)
    quote: str             # quote currency
    kind: str              # 'fx' | 'metal' | 'crypto'
    pip: float = 0.0001    # pip size for spread / display
    start_price: float = 1.0
    typical_spread_pips: float = 0.8
    description: str = ""
    lot_size_units: float = 100_000.0  # units of base asset per standard lot

    @property
    def display(self) -> str:
        if self.kind == "fx" and len(self.base) == 3 and len(self.quote) == 3:
            return f"{self.base}/{self.quote}"
        return self.symbol


# --- currency factor model inputs -------------------------------------------

CURRENCIES: dict[str, dict[str, float]] = {
    # beta_risk: sensitivity to the global risk-on/off factor
    # beta_comm: sensitivity to the commodity factor
    # vol: idiosyncratic volatility scale
    "USD": {"beta_risk": -0.15, "beta_comm": -0.10, "vol": 0.55},
    "EUR": {"beta_risk": 0.25, "beta_comm": -0.05, "vol": 0.75},
    "GBP": {"beta_risk": 0.35, "beta_comm": -0.05, "vol": 0.90},
    "JPY": {"beta_risk": -0.65, "beta_comm": -0.10, "vol": 0.80},
    "CHF": {"beta_risk": -0.55, "beta_comm": -0.05, "vol": 0.65},
    "AUD": {"beta_risk": 0.65, "beta_comm": 0.55, "vol": 1.05},
    "NZD": {"beta_risk": 0.60, "beta_comm": 0.50, "vol": 1.05},
    "CAD": {"beta_risk": 0.35, "beta_comm": 0.65, "vol": 0.85},
    "SEK": {"beta_risk": 0.40, "beta_comm": 0.15, "vol": 1.05},
    "NOK": {"beta_risk": 0.40, "beta_comm": 0.70, "vol": 1.15},
    "SGD": {"beta_risk": 0.15, "beta_comm": 0.10, "vol": 0.55},
    "MXN": {"beta_risk": 0.50, "beta_comm": 0.35, "vol": 1.45},
    "ZAR": {"beta_risk": 0.55, "beta_comm": 0.60, "vol": 1.60},
}

METALS: dict[str, dict[str, float]] = {
    "XAU": {"beta_risk": -0.35, "beta_comm": 0.25, "vol": 1.15},   # gold
    "XAG": {"beta_risk": 0.15, "beta_comm": 0.55, "vol": 1.80},    # silver
}


def _fx(symbol: str, base: str, quote: str, pip: float, start: float,
        spread: float, desc: str) -> Instrument:
    return Instrument(symbol, base, quote, "fx", pip, start, spread, desc)


# Majors ---------------------------------------------------------------------
MAJORS: list[Instrument] = [
    _fx("EURUSD", "EUR", "USD", 0.0001, 1.0850, 0.6, "Euro / US Dollar — the most liquid pair on Earth"),
    _fx("GBPUSD", "GBP", "USD", 0.0001, 1.2700, 0.9, "Cable — British Pound / US Dollar"),
    _fx("USDJPY", "USD", "JPY", 0.01, 149.50, 0.8, "US Dollar / Japanese Yen — key risk-barometer"),
    _fx("USDCHF", "USD", "CHF", 0.0001, 0.8800, 1.2, "US Dollar / Swiss Franc — classic safe-haven cross"),
    _fx("AUDUSD", "AUD", "USD", 0.0001, 0.6580, 0.9, "Aussie — commodity currency vs USD"),
    _fx("NZDUSD", "NZD", "USD", 0.0001, 0.6100, 1.3, "Kiwi — commodity currency vs USD"),
    _fx("USDCAD", "USD", "CAD", 0.0001, 1.3600, 1.1, "Loonie — oil-linked North-American cross"),
]

# Minor crosses ---------------------------------------------------------------
MINORS: list[Instrument] = [
    _fx("EURGBP", "EUR", "GBP", 0.0001, 0.8550, 1.0, "Euro / British Pound"),
    _fx("EURJPY", "EUR", "JPY", 0.01, 162.30, 1.2, "Euro / Japanese Yen — volatile carry favourite"),
    _fx("GBPJPY", "GBP", "JPY", 0.01, 189.90, 1.8, "The Beast / Dragon — high-volatility Yen cross"),
    _fx("EURCHF", "EUR", "CHF", 0.0001, 0.9550, 1.4, "Euro / Swiss Franc"),
    _fx("EURAUD", "EUR", "AUD", 0.0001, 1.6480, 1.6, "Euro / Australian Dollar"),
    _fx("EURNZD", "EUR", "NZD", 0.0001, 1.7780, 2.2, "Euro / New Zealand Dollar"),
    _fx("EURCAD", "EUR", "CAD", 0.0001, 1.4750, 1.7, "Euro / Canadian Dollar"),
    _fx("GBPCHF", "GBP", "CHF", 0.0001, 1.1180, 2.0, "British Pound / Swiss Franc"),
    _fx("GBPAUD", "GBP", "AUD", 0.0001, 1.9300, 2.2, "British Pound / Australian Dollar"),
    _fx("GBPCAD", "GBP", "CAD", 0.0001, 1.7280, 2.2, "British Pound / Canadian Dollar"),
    _fx("AUDJPY", "AUD", "JPY", 0.01, 98.40, 1.4, "Aussie / Yen — risk-sentiment proxy"),
    _fx("AUDNZD", "AUD", "NZD", 0.0001, 1.0780, 1.8, "Aussie / Kiwi"),
    _fx("NZDJPY", "NZD", "JPY", 0.01, 91.20, 1.8, "Kiwi / Yen"),
    _fx("CADJPY", "CAD", "JPY", 0.01, 110.00, 1.9, "Canadian Dollar / Yen"),
    _fx("CHFJPY", "CHF", "JPY", 0.01, 169.80, 2.0, "Swiss Franc / Yen"),
]

# Exotics ---------------------------------------------------------------------
EXOTICS: list[Instrument] = [
    _fx("USDMXN", "USD", "MXN", 0.0001, 17.20, 25.0, "US Dollar / Mexican Peso — EM carry"),
    _fx("USDZAR", "USD", "ZAR", 0.0001, 18.60, 45.0, "US Dollar / South African Rand — high-yield EM"),
    _fx("USDSGD", "USD", "SGD", 0.0001, 1.3450, 1.5, "US Dollar / Singapore Dollar"),
    _fx("USDSEK", "USD", "SEK", 0.0001, 10.85, 8.0, "US Dollar / Swedish Krona"),
    _fx("USDNOK", "USD", "NOK", 0.0001, 10.95, 12.0, "US Dollar / Norwegian Krone — oil-linked"),
    _fx("EURSEK", "EUR", "SEK", 0.0001, 11.75, 15.0, "Euro / Swedish Krona"),
    _fx("EURNOK", "EUR", "NOK", 0.0001, 11.85, 20.0, "Euro / Norwegian Krone"),
    _fx("EURMXN", "EUR", "MXN", 0.0001, 18.70, 35.0, "Euro / Mexican Peso"),
]

# Metals quoted against USD (traded like FX on most retail platforms) ---------
METAL_INSTRUMENTS: list[Instrument] = [
    Instrument("XAUUSD", "XAU", "USD", kind="metal", pip=0.01, start_price=2320.0,
               typical_spread_pips=30.0,        # 30 pips × 0.01 = 0.30 USD per oz
               description="Gold / US Dollar — the classic macro hedge",
               lot_size_units=100.0),
    Instrument("XAGUSD", "XAG", "USD", kind="metal", pip=0.001, start_price=27.40,
               typical_spread_pips=30.0,        # 30 pips × 0.001 = 0.030 USD per oz
               description="Silver / US Dollar — higher-beta precious metal",
               lot_size_units=5_000.0),
]

UNIVERSE: list[Instrument] = MAJORS + MINORS + EXOTICS + METAL_INSTRUMENTS

BY_SYMBOL: dict[str, Instrument] = {ins.symbol: ins for ins in UNIVERSE}


# --- broker quoting ----------------------------------------------------------
# Brokers quote and accept prices to a fixed number of decimals: 5 for most FX
# pairs, 3 for JPY crosses, 2 for gold and 3 for silver. A signal price with more
# decimals is not a price the broker can fill, so every plan price is rounded here.
MIN_LOT = 0.01          # smallest order size on standard retail accounts (lot steps of 0.01)


def price_digits(symbol: str) -> int:
    """Decimals a broker quotes ``symbol`` with."""
    ins = BY_SYMBOL.get(symbol)
    if ins is None:
        return 5
    if ins.kind == "metal":
        return 2 if ins.symbol == "XAUUSD" else 3
    return 3 if ins.quote == "JPY" else 5


def format_price(symbol: str, value: float) -> str:
    """A price as the broker shows it, e.g. 149.123 for USDJPY, 2320.45 for gold."""
    return f"{float(value):.{price_digits(symbol)}f}"


def round_price(symbol: str, value: float, *, away_from: float | None = None) -> float:
    """Round ``value`` to the broker's decimals. With ``away_from``, round in the
    direction away from that price (used for stops, so rounding never shrinks risk)."""
    digits = price_digits(symbol)
    scale = 10 ** digits
    x = float(value) * scale
    if away_from is not None:
        x = math.floor(x + 1e-9) if float(value) < float(away_from) else math.ceil(x - 1e-9)
        return round(x / scale, digits)
    return round(round(x) / scale, digits)


def default_universe(kinds: tuple[str, ...] = ("fx", "metal")) -> list[Instrument]:
    """Return the tradable universe filtered by instrument kinds."""
    return [ins for ins in UNIVERSE if ins.kind in kinds]
