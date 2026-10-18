"""FastAPI application: JSON API, landing page, paid-access dashboard, operator admin.

Public
------
GET  /                         landing page
GET  /app                      dashboard shell (data endpoints need paid access)
GET  /admin                    operator console (admin role required)
GET  /api/health               liveness + config summary
POST /api/register             create account (username, email, phone, password)
POST /api/auth/login           log in with username, email or phone + password
POST /api/auth/forgot          e-mail a reset code (never returned in the response)
POST /api/auth/reset           verify the code, set a new password, log in
GET  /api/auth/password-policy password rules for the UI

Logged in (bearer token)
------------------------
GET  /api/me                   account, access status, my payments
POST /api/auth/logout          end the session
GET  /api/payment-options      plans, prices and which payment methods are set up
POST /api/payments             create an invoice (crypto or wire)
GET  /api/payments             my invoices
POST /api/payments/{id}/reference  submit the transaction hash / wire reference

Paid access (active subscription or admin)
------------------------------------------
GET  /api/scan, /api/instructions, /api/pair/{symbol}, /api/universe, POST /api/backtest

Admin
-----
GET  /api/admin/payments, POST /api/admin/payments/{id}/confirm|reject
GET  /api/admin/users, POST /api/admin/users/{id}/grant|revoke
GET|PUT /api/admin/payment-settings   crypto wallets and wire bank details
GET  /api/admin/audit-log            recent operator activity
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import billing
from .auth import (  # noqa: F401  (re-exported for tests)
    ACCOUNTS,
    LockedError,
    deliver_reset_code,
    dev_reset_codes_enabled,
    issue_session,
    password_policy,
    password_strength,
    policy_error,
    resolve_session,
    revoke_session,
)
from .backtest import run_backtest
from .config import AppConfig
from .db import User
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
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# live market data is on by default; KFX_LIVE_DATA=0 forces the synthetic market.
# When live data is unreachable the engine falls back to synthetic automatically
# and the dashboard shows the reason.
_engine = MarketEngine(AppConfig(), prefer_live=_env_flag("KFX_LIVE_DATA", True))
_engine_lock = threading.Lock()


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="e.g. EURUSD")
    equity: Optional[float] = Field(None, gt=0)
    risk_per_trade_pct: Optional[float] = Field(None, gt=0, le=5)
    bars: Optional[int] = Field(None, ge=250, le=20000)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: str = Field(..., min_length=5, max_length=200)
    phone: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=1, max_length=128)


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


class PaymentRequest(BaseModel):
    plan: str = Field(..., description="starter | pro | elite")
    method: Literal["crypto", "wire"]
    network: Optional[str] = Field(None, max_length=16, description="crypto only, e.g. BTC")


class ReferenceRequest(BaseModel):
    reference: str = Field(..., min_length=4, max_length=200,
                           description="transaction hash (crypto) or wire reference")
    sender_note: str = Field("", max_length=300)


class DecisionRequest(BaseModel):
    note: str = Field("", max_length=300)


class RejectRequest(BaseModel):
    note: str = Field(..., min_length=3, max_length=300, description="reason shown to the buyer")


class GrantRequest(BaseModel):
    plan: str = Field(..., description="starter | pro | elite")
    days: int = Field(billing.ACCESS_DAYS, ge=1, le=366)
    note: str = Field(..., min_length=3, max_length=300)


class CryptoEntry(BaseModel):
    network: str = Field("", max_length=16)
    address: str = Field("", max_length=120)


class WireDetails(BaseModel):
    bank_name: str = Field("", max_length=300)
    account_name: str = Field("", max_length=300)
    account_number: str = Field("", max_length=300)
    routing_or_swift: str = Field("", max_length=300)
    iban: str = Field("", max_length=300)
    instructions: str = Field("", max_length=300)


class PaymentSettingsRequest(BaseModel):
    crypto: list[CryptoEntry] = Field(default_factory=list)
    wire: WireDetails = Field(default_factory=WireDetails)


# ---------------------------------------------------------------------------
# auth dependencies
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> User:
    user = resolve_session(creds.credentials if creds else None)
    if user is None:
        raise HTTPException(status_code=401, detail="log in to continue")
    return user


def require_access(user: User = Depends(current_user)) -> User:
    """Paid access: an active subscription window, or the admin role."""
    if user.role == "admin" or billing.access_status(user.id)["active"]:
        return user
    raise HTTPException(status_code=403, detail="payment required — complete payment to unlock the bot")


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _user_public(user: User) -> dict[str, Any]:
    return {"id": user.id, "username": user.username, "email": user.email,
            "phone": user.phone or "", "role": user.role}


def _session_payload(user: User) -> dict[str, Any]:
    return {
        "user": _user_public(user),
        "access": billing.access_status(user.id),
    }


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------

@app.post("/api/register")
def register(req: RegisterRequest) -> dict[str, Any]:
    """Create an account.  Access is granted only after a confirmed payment."""
    try:
        user = ACCOUNTS.register(req.username, req.email, req.phone, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = issue_session(user.id)
    return {
        "ok": True,
        "token": token,
        **_session_payload(user),
        "message": "Account created. Choose a plan and complete payment to unlock the bot.",
    }


@app.post("/api/auth/login")
def auth_login(req: AuthLoginRequest) -> dict[str, Any]:
    try:
        user = ACCOUNTS.authenticate(req.identifier, req.password)
    except LockedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        # same answer for unknown account and wrong password: no account enumeration
        raise HTTPException(status_code=401,
                            detail="incorrect username, email, phone or password") from exc
    token = issue_session(user.id)
    return {"ok": True, "token": token, **_session_payload(user), "access_url": "/app"}


@app.post("/api/auth/logout")
def auth_logout(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict[str, Any]:
    revoke_session(creds.credentials if creds else None)
    return {"ok": True}


@app.post("/api/auth/forgot")
def auth_forgot(req: ForgotRequest) -> dict[str, Any]:
    """Always answers the same way, so it cannot be used to discover accounts."""
    message = "If an account exists for that email, a reset code has been sent. It expires in 15 minutes."
    try:
        code = ACCOUNTS.issue_reset(req.email)
    except KeyError:
        return {"ok": True, "message": message}
    # delivery status is logged, never returned: the response must not depend on the account
    delivered = deliver_reset_code(req.email.strip().lower(), code)
    if not delivered:
        logging.getLogger("klevs.auth").warning("reset code was not delivered (SMTP not configured or failed)")
    payload: dict[str, Any] = {"ok": True, "message": message}
    if dev_reset_codes_enabled():
        payload["dev_code"] = code          # local development only (KLEVS_DEV_RESET_CODES=1)
    return payload


@app.post("/api/auth/reset")
def auth_reset(req: ResetRequest) -> dict[str, Any]:
    try:
        user = ACCOUNTS.reset_password(req.email, req.code, req.new_password)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = issue_session(user.id)
    return {"ok": True, "message": "Password updated. You are logged in with the new password.",
            "token": token, **_session_payload(user), "access_url": "/app"}


@app.get("/api/auth/password-policy")
def auth_password_policy() -> dict[str, Any]:
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


@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {**_session_payload(user),
            "payments": billing.my_payments(user.id)[:10],
            "methods": billing.available_methods()}


# ---------------------------------------------------------------------------
# payments (user side)
# ---------------------------------------------------------------------------

@app.get("/api/payment-options")
def payment_options(_user: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "plans": {k: {"name": v["name"], "price_usd": v["price_usd"], "days": billing.ACCESS_DAYS}
                  for k, v in billing.PLANS.items()},
        "methods": billing.available_methods(),
        "access_days": billing.ACCESS_DAYS,
    }


@app.post("/api/payments")
def create_payment(req: PaymentRequest, user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        payment = billing.create_payment(user.id, req.plan, req.method, req.network)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "payment": payment}


@app.get("/api/payments")
def list_my_payments(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"payments": billing.my_payments(user.id)}


@app.post("/api/payments/{payment_id}/reference")
def submit_reference(payment_id: int, req: ReferenceRequest,
                     user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        payment = billing.submit_reference(user.id, payment_id, req.reference, req.sender_note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "payment": payment,
            "message": "Submitted. The operator will verify the payment and unlock your access."}


# ---------------------------------------------------------------------------
# admin (operator console)
# ---------------------------------------------------------------------------

@app.get("/api/admin/payments")
def admin_payments(status: Optional[str] = None, _admin: User = Depends(require_admin)) -> dict[str, Any]:
    return {"payments": billing.list_payments(status)}


@app.post("/api/admin/payments/{payment_id}/confirm")
def admin_confirm(payment_id: int, req: Optional[DecisionRequest] = None,
                  admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        payment = billing.confirm_payment(payment_id, admin.username, req.note if req else "")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "payment": payment}


@app.post("/api/admin/payments/{payment_id}/reject")
def admin_reject(payment_id: int, req: RejectRequest, admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        payment = billing.reject_payment(payment_id, admin.username, req.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "payment": payment}


@app.get("/api/admin/users")
def admin_users(_admin: User = Depends(require_admin)) -> dict[str, Any]:
    return {"users": billing.list_users()}


@app.post("/api/admin/users/{user_id}/grant")
def admin_grant(user_id: int, req: GrantRequest, admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        grant = billing.grant_access(user_id, req.plan, req.days, admin.username, req.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "grant": grant}


@app.post("/api/admin/users/{user_id}/revoke")
def admin_revoke(user_id: int, req: RejectRequest, admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        count = billing.revoke_access(user_id, admin.username, req.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "revoked": count}


@app.get("/api/admin/payment-settings")
def admin_get_settings(_admin: User = Depends(require_admin)) -> dict[str, Any]:
    return billing.get_settings()


@app.put("/api/admin/payment-settings")
def admin_put_settings(req: PaymentSettingsRequest,
                       admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        data = billing.update_settings(req.model_dump(), admin.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "settings": data}


@app.get("/api/admin/audit-log")
def admin_audit_log(limit: int = Query(100, ge=1, le=500),
                    _admin: User = Depends(require_admin)) -> dict[str, Any]:
    return {"events": billing.list_admin_audit(limit)}


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
def universe(_user: User = Depends(require_access)) -> list[dict[str, Any]]:
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
def scan(refresh: bool = False, _user: User = Depends(require_access)) -> dict[str, Any]:
    with _engine_lock:
        report = _engine.scan(use_cache=not refresh, reload=refresh)
    return report.to_dict()


@app.get("/api/instructions")
def instructions(refresh: bool = False, _user: User = Depends(require_access)) -> dict[str, Any]:
    """Distilled command stream: when to buy, predicted outcome, when to sell."""
    with _engine_lock:
        report = _engine.scan(use_cache=not refresh, reload=refresh)
    d = report.to_dict()
    return {
        "generated_at": d["generated_at"],
        "data_source": d["data_source"],
        "instructions": d["instructions"],
    }


@app.get("/api/pair/{symbol}")
def pair(symbol: str, _user: User = Depends(require_access)) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol}")
    with _engine_lock:
        payload = _engine.pair_report(symbol)
    if payload is None:
        raise HTTPException(status_code=404, detail="no data")
    return payload


@app.post("/api/backtest")
def backtest(req: BacktestRequest, _user: User = Depends(require_access)) -> dict[str, Any]:
    symbol = req.symbol.upper()
    if symbol not in BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol}")
    cfg = AppConfig()
    if req.equity is not None:
        cfg.risk.equity = req.equity
    if req.risk_per_trade_pct is not None:
        cfg.risk.risk_per_trade_pct = req.risk_per_trade_pct

    # Loading/reading the shared history and the backtest itself must be covered
    # by the same engine lock used by scans and pair reports.
    with _engine_lock:
        history = _engine.history
        df = history.get(symbol)
        if df is None:
            raise HTTPException(status_code=404, detail="no data")
        if req.bars:
            df = df.tail(req.bars)
        try:
            result = run_backtest(symbol, df, cfg, conversion_history=history)
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
                "signal_time": str(t.signal_time),
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


@app.get("/admin")
def admin_console() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "admin.html",
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:  # pragma: no cover
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":  # pragma: no cover
    main()

