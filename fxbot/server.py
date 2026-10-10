"""FastAPI application: JSON API + self-contained web dashboard.

Endpoints
---------
GET  /                      dashboard (static single-page app)
GET  /api/health            liveness + config summary
GET  /api/scan              full-market scan report (signals, insights, strength)
GET  /api/pair/{symbol}     deep-dive: candles, indicators, plan, forecast
POST /api/backtest          run a backtest for a symbol
GET  /api/universe          instrument list
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .backtest import run_backtest
from .config import AppConfig
from .engine import MarketEngine
from .universe import BY_SYMBOL, UNIVERSE

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="klevs FX trading App",
    version="1.0.0",
    description="klevs FX trading App — scans the entire FX market, forecasts direction, "
                "and produces entry / take-profit / stop-loss trade plans with risk sizing.",
)

# one shared engine — scan is cached until `refresh`
_engine = MarketEngine(AppConfig())


class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="e.g. EURUSD")
    equity: Optional[float] = Field(None, gt=0)
    risk_per_trade_pct: Optional[float] = Field(None, gt=0, le=5)
    bars: Optional[int] = Field(None, ge=200, le=20000)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "1.0.0",
        "data_source": _engine.data.source_label,
        "universe_size": len(UNIVERSE),
        "config": _engine.cfg.to_dict(),
    }


@app.get("/api/universe")
def universe() -> list[dict[str, Any]]:
    return [
        {
            "symbol": i.symbol,
            "display": i.display,
            "kind": i.kind,
            "description": i.description,
            "pip": i.pip,
            "typical_spread_pips": i.typical_spread_pips,
        }
        for i in UNIVERSE
    ]


@app.get("/api/scan")
def scan(refresh: bool = False) -> dict[str, Any]:
    report = _engine.scan(use_cache=not refresh)
    return report.to_dict()


@app.get("/api/instructions")
def instructions(refresh: bool = False) -> dict[str, Any]:
    """Distilled command stream: when to buy, predicted outcome, when to sell."""
    report = _engine.scan(use_cache=not refresh)
    d = report.to_dict()
    return {
        "generated_at": d["generated_at"],
        "data_source": d["data_source"],
        "instructions": d["instructions"],
    }


@app.get("/api/pair/{symbol}")
def pair(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol}")
    payload = _engine.pair_report(symbol)
    if payload is None:
        raise HTTPException(status_code=404, detail="no data")
    return payload


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict[str, Any]:
    symbol = req.symbol.upper()
    if symbol not in BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol}")
    df = _engine.pair_frame(symbol)
    if df is None:
        raise HTTPException(status_code=404, detail="no data")
    if req.bars:
        df = df.tail(req.bars)

    cfg = AppConfig()
    if req.equity:
        cfg.risk.equity = req.equity
    if req.risk_per_trade_pct:
        cfg.risk.risk_per_trade_pct = req.risk_per_trade_pct

    result = run_backtest(symbol, df, cfg)
    curve = result.equity_curve
    return {
        "symbol": symbol,
        "metrics": result.metrics,
        "equity_curve": {
            "t": [int(ts.timestamp()) for ts in curve.index[:: max(1, len(curve) // 400)]],
            "v": [round(float(v), 2) for v in curve.iloc[:: max(1, len(curve) // 400)]],
        },
        "trades": [
            {
                "side": t.side,
                "entry_time": str(t.entry_time),
                "entry": round(t.entry, 6),
                "exit_time": str(t.exit_time),
                "exit": round(t.exit, 6),
                "stop": round(t.stop, 6),
                "target": round(t.target, 6),
                "r_multiple": round(t.r_multiple, 2),
                "pnl": round(t.pnl, 2),
                "exit_reason": t.exit_reason,
            }
            for t in result.trades[-80:]
        ],
    }


# -- static dashboard --------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:  # pragma: no cover
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":  # pragma: no cover
    main()
