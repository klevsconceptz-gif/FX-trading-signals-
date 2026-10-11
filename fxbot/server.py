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

from .auth import (  # noqa: F401  (re-exported for tests)
    ACCOUNTS,
    password_policy,
    password_strength,
    policy_error,
)
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
    # operator-only: granted to the admin account, never offered at checkout
    "admin": {"name": "Admin", "price_usd": 0, "interval": "month", "access": "unrestricted"},
}
SELF_SERVE_PLANS = ("starter", "pro", "elite", "demo")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SUBSCRIPTIONS: dict[str, dict[str, Any]] = {}   # token -> record
_EMAIL_INDEX: dict[str, str] = {}                # lowercase email -> token
_SUB_LOCK = threading.Lock()


class SubscribeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=5, max_length=200)
    plan: str = Field("pro", description="starter | pro | elite | demo")


class RegisterRequest(BaseModel):
    """Full registration: the four allowed credentials + chosen plan."""
    username: str = Field(..., min_length=3, max_length=32)
    email: str = Field(..., min_length=5, max_length=200)
    phone: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=1, max_length=128)
    plan: str = Field("pro", description="starter | pro | elite | demo")


class AuthLoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=200,
                            description="username, email or phone number")
    password: str = Field(..., min_length=1, max_length=128)


class ForgotRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)


class ResetRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)
    code: str = Field(..., min_length=6, max_length=10)
    new_password: str = Field(..., min_length=1, max_length=128)


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
    plan = PLANS.get(req.plan) if req.plan in SELF_SERVE_PLANS else None
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


def _issue_session(email: str, username: str, phone: str, plan_key: str,
                   created_at: Optional[float] = None, role: str = "user") -> dict[str, Any]:
    """Create / refresh a subscription session for an account and return its token."""
    plan = PLANS.get(plan_key) or PLANS["demo"]
    if role == "admin":
        plan_key = "admin"
        plan = PLANS["admin"]
    with _SUB_LOCK:
        # retire any previous session for this account
        old = _EMAIL_INDEX.get(email)
        if old is not None:
            _SUBSCRIPTIONS.pop(old, None)
        token = secrets.token_urlsafe(24)
        record = {
            "token": token,
            "name": username,
            "username": username,
            "email": email,
            "phone": phone,
            "plan": plan_key,
            "plan_name": plan["name"],
            "price_usd": plan["price_usd"],
            "interval": plan["interval"],
            "role": role,
            "created_at": created_at or time.time(),
            "renews_at": time.time() + 30 * 86400,
        }
        if plan.get("access"):
            record["access"] = plan["access"]
        record = _checkout(record)
        _SUBSCRIPTIONS[token] = record
        _EMAIL_INDEX[email] = token
        return {"token": token, "record": record}


@app.post("/api/register")
def register(req: RegisterRequest) -> dict[str, Any]:
    """Registration attached to the paywall: creates the account (username,
    email, phone, password) and activates the chosen monthly plan."""
    plan = PLANS.get(req.plan) if req.plan in SELF_SERVE_PLANS else None
    if plan is None:
        raise HTTPException(status_code=422, detail=f"unknown plan '{req.plan}' — choose starter, pro or elite")
    try:
        acc = ACCOUNTS.register(req.username, req.email, req.phone, req.password, plan=req.plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = _issue_session(acc.email, acc.username, acc.phone, req.plan, acc.created_at, acc.role)
    return {
        "ok": True,
        "token": session["token"],
        "subscription": _public_record(session["record"]),
        "message": f"Account created and {plan['name']} plan active — welcome to klevs FX trading App. "
                   "Demo checkout: no payment was processed; wire Stripe Checkout into "
                   "fxbot/server.py::_checkout for live billing.",
        "access_url": "/app",
    }


@app.post("/api/auth/login")
def auth_login(req: AuthLoginRequest) -> dict[str, Any]:
    """Log in with any credential — username, email or phone number — + password."""
    try:
        acc = ACCOUNTS.authenticate(req.identifier, req.password)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    session = _issue_session(acc.email, acc.username, acc.phone, acc.plan, acc.created_at, acc.role)
    return {
        "ok": True,
        "token": session["token"],
        "subscription": _public_record(session["record"]),
        "access_url": "/app",
    }


@app.post("/api/auth/forgot")
def auth_forgot(req: ForgotRequest) -> dict[str, Any]:
    """Email auth: issue a one-time reset code. Only the code's hash is stored;
    in production the raw code is emailed to the account owner."""
    try:
        code = ACCOUNTS.issue_reset(req.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="no account found for that email") from None
    return {
        "ok": True,
        "message": f"A 7-digit reset code has been sent to {req.email}. "
                   "It expires in 15 minutes. (Demo build: no real email is sent — "
                   "use the dev_code below to complete the flow; production delivers "
                   "it via your email provider only.)",
        "dev_code": code,          # demo-only delivery channel; remove in production
        "expires_in_seconds": 900,
    }


@app.post("/api/auth/reset")
def auth_reset(req: ResetRequest) -> dict[str, Any]:
    """Complete forgot-password: verify the emailed code and set a new strong password."""
    try:
        acc = ACCOUNTS.reset_password(req.email, req.code, req.new_password)
    except KeyError:
        raise HTTPException(status_code=404, detail="no reset was requested for that email") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = _issue_session(acc.email, acc.username, acc.phone, acc.plan, acc.created_at, acc.role)
    return {
        "ok": True,
        "message": "Password updated. You are logged in with the new password.",
        "token": session["token"],
        "subscription": _public_record(session["record"]),
        "access_url": "/app",
    }


@app.get("/api/auth/password-policy")
def auth_password_policy() -> dict[str, Any]:
    """Static policy description for the UI checklist/strength meter."""
    return {
        "min_length": 10,
        "rules": ["length_10", "uppercase", "lowercase", "number", "symbol"],
        "labels": {
            "length_10": "At least 10 characters",
            "uppercase": "An uppercase letter (A-Z)",
            "lowercase": "A lowercase letter (a-z)",
            "number": "A number (0-9)",
            "symbol": "A symbol (!@#$%…)",
        },
    }


@app.get("/api/subscription/{token}")
def subscription(token: str) -> dict[str, Any]:
    with _SUB_LOCK:
        record = _SUBSCRIPTIONS.get(token)
        if record is None or record.get("status") != "active":
            raise HTTPException(status_code=404, detail="no active subscription")
        return {"ok": True, "subscription": _public_record(record)}


def _bootstrap_admin_from_env() -> Optional[str]:
    """Provision the admin account from the environment (no credentials in code).

        KFX_ADMIN_USERNAME  required, e.g. Admin@klev.fx
        KFX_ADMIN_PASSWORD  required, must satisfy the password policy
        KFX_ADMIN_EMAIL     optional (defaults to the username)
        KFX_ADMIN_PHONE     optional

    Accounts live in memory, so this runs on every start.  Returns the admin
    username, or None when no admin is configured.  Misconfiguration raises so
    the server refuses to start with a half-provisioned admin.
    """
    import os

    username = os.environ.get("KFX_ADMIN_USERNAME", "").strip()
    password = os.environ.get("KFX_ADMIN_PASSWORD", "")
    if not username and not password:
        return None
    if not username or not password:
        raise RuntimeError("set both KFX_ADMIN_USERNAME and KFX_ADMIN_PASSWORD to provision the admin")
    ACCOUNTS.seed_admin(
        username,
        password,
        email=os.environ.get("KFX_ADMIN_EMAIL") or None,
        phone=os.environ.get("KFX_ADMIN_PHONE") or None,
    )
    return username


_bootstrap_admin_from_env()


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
