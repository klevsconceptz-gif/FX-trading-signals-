"""FastAPI application: JSON API + landing page + subscriber dashboard.

Endpoints
---------
GET  /                      marketing landing page (subscribe monthly)
GET  /app                   the bot dashboard (subscriber-gated in the UI)
GET  /api/health            liveness + config summary
GET  /api/scan              full-market scan report (signals, insights, strength)
GET  /api/instructions      distilled buy → predict → sell command stream
GET  /api/pair/{symbol}     deep-dive: candles, indicators, plan, forecast
POST /api/backtest          run a backtest for a symbol
GET  /api/universe          instrument list
POST /api/subscribe         monthly subscription signup (demo checkout)
POST /api/login             re-issue access token for a subscribed email
GET  /api/subscription/{token}  subscription status
"""

from __future__ import annotations

import re
import secrets
import threading
import time
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

# one shared engine — scan is cached until `refresh`.  Sync endpoints run in
# FastAPI's threadpool, so all engine access is serialized with this lock.
_engine = MarketEngine(AppConfig())
_engine_lock = threading.Lock()


# ---------------------------------------------------------------------------
# subscriptions (demo checkout — replace _checkout with Stripe/Paddle in prod)
# ---------------------------------------------------------------------------

PLANS: dict[str, dict[str, Any]] = {
    "starter": {"name": "Starter", "price_usd": 29, "interval": "month"},
    "pro": {"name": "Pro", "price_usd": 79, "interval": "month"},
    "elite": {"name": "Elite", "price_usd": 199, "interval": "month"},
    "demo": {"name": "Demo", "price_usd": 0, "interval": "month"},
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SUBSCRIPTIONS: dict[str, dict[str, Any]] = {}   # token -> record
_EMAIL_INDEX: dict[str, str] = {}                # lowercase email -> token
_SUB_LOCK = threading.Lock()


class SubscribeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=5, max_length=200)
    plan: str = Field("pro", description="starter | pro | elite | demo")


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)


def _checkout(record: dict[str, Any]) -> dict[str, Any]:
    """Demo checkout: marks the subscription active without charging anything.

    Production: create a Stripe Checkout Session (or Paddle transaction) here,
    return its hosted URL, and mark the subscription active from the webhook.
    """
    record["status"] = "active"
    record["billing"] = "demo-checkout (no payment processed)"
    return record


class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="e.g. EURUSD")
    equity: Optional[float] = Field(None, gt=0)
    risk_per_trade_pct: Optional[float] = Field(None, gt=0, le=5)
    bars: Optional[int] = Field(None, ge=250, le=20000)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": "klevs FX trading App",
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
    with _engine_lock:
        report = _engine.scan(use_cache=not refresh)
    return report.to_dict()


@app.get("/api/instructions")
def instructions(refresh: bool = False) -> dict[str, Any]:
    """Distilled command stream: when to buy, predicted outcome, when to sell."""
    with _engine_lock:
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
    with _engine_lock:
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

    with _engine_lock:
        try:
            result = run_backtest(symbol, df, cfg)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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


# -- subscriptions ------------------------------------------------------------

@app.post("/api/subscribe")
def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="invalid email address")
    plan = PLANS.get(req.plan)
    if plan is None:
        raise HTTPException(status_code=422, detail=f"unknown plan '{req.plan}' — choose starter, pro or elite")

    with _SUB_LOCK:
        token = secrets.token_urlsafe(24)
        record = {
            "token": token,
            "name": req.name.strip(),
            "email": email,
            "plan": req.plan,
            "plan_name": plan["name"],
            "price_usd": plan["price_usd"],
            "interval": plan["interval"],
            "created_at": time.time(),
            "renews_at": time.time() + 30 * 86400,
        }
        record = _checkout(record)
        _SUBSCRIPTIONS[token] = record
        _EMAIL_INDEX[email] = token
    return {
        "ok": True,
        "token": token,
        "subscription": _public_record(record),
        "message": "Subscription active — welcome to klevs FX trading App. "
                   "Demo checkout: no payment was processed; wire Stripe Checkout into "
                   "fxbot/server.py::_checkout for live billing.",
        "access_url": "/app",
    }


@app.post("/api/login")
def login(req: LoginRequest) -> dict[str, Any]:
    email = req.email.strip().lower()
    with _SUB_LOCK:
        old_token = _EMAIL_INDEX.get(email)
        record = _SUBSCRIPTIONS.get(old_token) if old_token else None
        if record is None:
            raise HTTPException(status_code=404, detail="no subscription found for that email")
        new_token = secrets.token_urlsafe(24)   # re-issue a session token
        record["token"] = new_token
        _SUBSCRIPTIONS.pop(old_token, None)
        _SUBSCRIPTIONS[new_token] = record
        _EMAIL_INDEX[email] = new_token
    return {
        "ok": True,
        "token": new_token,
        "subscription": _public_record(record),
        "access_url": "/app",
    }


@app.get("/api/subscription/{token}")
def subscription(token: str) -> dict[str, Any]:
    with _SUB_LOCK:
        record = _SUBSCRIPTIONS.get(token)
        if record is None or record.get("status") != "active":
            raise HTTPException(status_code=404, detail="no active subscription")
        return {"ok": True, "subscription": _public_record(record)}


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "token"}


# -- pages --------------------------------------------------------------------

@app.get("/")
def landing() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "landing.html",
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/app")
def dashboard() -> FileResponse:
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
