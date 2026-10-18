"""Tradable universe: FX pairs, metals and FX-adjacent assets.

The same module powers the synthetic factor model, the live-data adapters
and the scanner, so adding an instrument here extends the entire platform.
"""

from __future__ import annotations

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
               typical_spread_pips=0.30,
               description="Gold / US Dollar — the classic macro hedge"),
    Instrument("XAGUSD", "XAG", "USD", kind="metal", pip=0.001, start_price=27.40,
               typical_spread_pips=0.025,
               description="Silver / US Dollar — higher-beta precious metal"),
]

UNIVERSE: list[Instrument] = MAJORS + MINORS + EXOTICS + METAL_INSTRUMENTS

BY_SYMBOL: dict[str, Instrument] = {ins.symbol: ins for ins in UNIVERSE}


def default_universe(kinds: tuple[str, ...] = ("fx", "metal")) -> list[Instrument]:
    """Return the tradable universe filtered by instrument kinds."""
    return [ins for ins in UNIVERSE if ins.kind in kinds]
