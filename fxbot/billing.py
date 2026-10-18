"""Payments and paid access.

There is no card processor.  Every payment goes to a wallet or bank account
that the operator configures (crypto wallet addresses, or wire-transfer bank
details).  The flow is:

1. the user picks a plan and a method and receives an invoice code;
2. the user pays the shown wallet / bank account, quoting the invoice code;
3. the user submits the transaction hash (crypto) or wire reference;
4. the operator checks the payment in their own wallet / bank and confirms it
   in the admin page, which grants ``ACCESS_DAYS`` of access;
5. access can be granted or revoked by the operator at any time.

Nothing here verifies blockchain or bank transactions automatically: that
needs explorer / bank API access, which this build does not have.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any, Optional

from sqlalchemy import select, update

from .db import AccessGrant, Payment, Setting, User, db_session

PLANS: dict[str, dict[str, Any]] = {
    "starter": {"name": "Starter", "price_usd": 29},
    "pro": {"name": "Pro", "price_usd": 79},
    "elite": {"name": "Elite", "price_usd": 199},
}
ACCESS_DAYS = 30
METHODS = ("crypto", "wire")
SETTINGS_KEY = "payment_settings"
OPEN_STATUSES = ("awaiting_payment", "submitted")
WIRE_FIELDS = ("bank_name", "account_name", "account_number", "routing_or_swift", "iban", "instructions")


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# operator-managed payment settings
# ---------------------------------------------------------------------------

def _empty_settings() -> dict[str, Any]:
    return {"crypto": [], "wire": dict.fromkeys(WIRE_FIELDS, "")}


def get_settings() -> dict[str, Any]:
    with db_session() as s:
        row = s.get(Setting, SETTINGS_KEY)
        raw = json.loads(row.value) if row else {}
    data = _empty_settings()
    data["crypto"] = list(raw.get("crypto", []))
    data["wire"].update({k: v for k, v in (raw.get("wire") or {}).items() if k in WIRE_FIELDS})
    return data


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    crypto: list[dict[str, str]] = []
    for item in payload.get("crypto") or []:
        network = str(item.get("network", "")).strip().upper()
        address = str(item.get("address", "")).strip()
        if not network and not address:
            continue
        if not network or not address:
            raise ValueError("each crypto entry needs a network and a wallet address")
        if not re.fullmatch(r"[A-Z0-9.\-]{2,16}", network):
            raise ValueError(f"invalid network name '{network}' (e.g. BTC, ETH, USDT-TRC20)")
        if not re.fullmatch(r"[A-Za-z0-9]{8,120}", address):
            raise ValueError(f"invalid {network} wallet address")
        if any(c["network"] == network for c in crypto):
            raise ValueError(f"network {network} is listed twice")
        crypto.append({"network": network, "address": address})

    wire_in = payload.get("wire") or {}
    wire = {f: str(wire_in.get(f, "")).strip()[:300] for f in WIRE_FIELDS}
    if any(wire.values()) and not (wire["account_number"] or wire["iban"]):
        raise ValueError("wire details need an account number or an IBAN")

    data = {"crypto": crypto, "wire": wire}
    with db_session() as s:
        row = s.get(Setting, SETTINGS_KEY)
        if row is None:
            s.add(Setting(key=SETTINGS_KEY, value=json.dumps(data)))
        else:
            row.value = json.dumps(data)
    return data


def available_methods(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "crypto": {
            "available": bool(settings["crypto"]),
            "networks": [c["network"] for c in settings["crypto"]],
        },
        "wire": {
            "available": bool(settings["wire"]["account_number"] or settings["wire"]["iban"]),
        },
    }


# ---------------------------------------------------------------------------
# payments (user side)
# ---------------------------------------------------------------------------

def _new_invoice(session) -> str:
    while True:
        code = "KFX-" + secrets.token_hex(3).upper()
        if session.scalar(select(Payment.id).where(Payment.invoice == code)) is None:
            return code


def _view(p: Payment, plan_name: Optional[str] = None) -> dict[str, Any]:
    pay_to = json.loads(p.pay_to) if p.pay_to else {}
    return {
        "id": p.id,
        "invoice": p.invoice,
        "plan": p.plan,
        "plan_name": plan_name or PLANS.get(p.plan, {}).get("name", p.plan),
        "amount_usd": p.amount_usd,
        "method": p.method,
        "network": p.network,
        "pay_to": pay_to,
        "status": p.status,
        "reference": p.reference,
        "sender_note": p.sender_note,
        "created_at": p.created_at,
        "submitted_at": p.submitted_at,
        "decided_at": p.decided_at,
        "decided_by": p.decided_by,
        "decision_note": p.decision_note,
        "payment_instruction": (
            f"Quote invoice {p.invoice} in the payment note / memo."
            if p.method == "wire" else
            f"Send exactly ${p.amount_usd:.2f} worth of {p.network} to the address shown, "
            f"then submit the transaction hash below."
        ),
    }


def create_payment(user_id: int, plan: str, method: str, network: Optional[str] = None) -> dict[str, Any]:
    if plan not in PLANS:
        raise ValueError(f"unknown plan '{plan}' — choose starter, pro or elite")
    if method not in METHODS:
        raise ValueError("method must be 'crypto' or 'wire'")
    settings = get_settings()

    if method == "crypto":
        wallets = {c["network"]: c["address"] for c in settings["crypto"]}
        if not wallets:
            raise ValueError("crypto payments are not set up yet — the operator has not added a wallet")
        network = (network or "").strip().upper()
        if network not in wallets:
            raise ValueError(f"choose a network: {', '.join(wallets)}")
        pay_to = {"network": network, "address": wallets[network]}
    else:
        wire = settings["wire"]
        if not (wire["account_number"] or wire["iban"]):
            raise ValueError("wire transfer is not set up yet — the operator has not added bank details")
        network = None
        pay_to = {k: v for k, v in wire.items() if v}

    with db_session() as s:
        open_rows = s.scalars(select(Payment).where(
            Payment.user_id == user_id, Payment.status.in_(OPEN_STATUSES))).all()
        for existing in open_rows:
            if existing.status == "submitted":
                raise ValueError(f"invoice {existing.invoice} is awaiting review — "
                                 "wait for the operator's decision before starting another payment")
        for existing in open_rows:
            existing.status = "cancelled"          # replaced by this new invoice

        payment = Payment(
            user_id=user_id,
            invoice=_new_invoice(s),
            plan=plan,
            amount_usd=float(PLANS[plan]["price_usd"]),
            method=method,
            network=network,
            pay_to=json.dumps(pay_to),
            status="awaiting_payment",
            created_at=_now(),
        )
        s.add(payment)
        s.flush()
        return _view(payment)


def submit_reference(user_id: int, payment_id: int, reference: str, sender_note: str = "") -> dict[str, Any]:
    reference = reference.strip()
    if not (4 <= len(reference) <= 200):
        raise ValueError("enter the transaction hash or wire reference (4-200 characters)")
    with db_session() as s:
        p = s.get(Payment, payment_id)
        if p is None or p.user_id != user_id:
            raise LookupError("payment not found")
        if p.status != "awaiting_payment":
            raise ValueError(f"this invoice is {p.status.replace('_', ' ')} — nothing to submit")
        p.reference = reference
        p.sender_note = (sender_note or "").strip()[:300] or None
        p.status = "submitted"
        p.submitted_at = _now()
        s.flush()
        return _view(p)


def my_payments(user_id: int) -> list[dict[str, Any]]:
    with db_session() as s:
        rows = s.scalars(select(Payment).where(Payment.user_id == user_id)
                         .order_by(Payment.created_at.desc())).all()
        return [_view(p) for p in rows]


# ---------------------------------------------------------------------------
# access
# ---------------------------------------------------------------------------

def _active_grant_in(session, user_id: int) -> Optional[AccessGrant]:
    now = _now()
    return session.scalars(select(AccessGrant).where(
        AccessGrant.user_id == user_id,
        AccessGrant.status == "active",
        AccessGrant.expires_at > now,
    ).order_by(AccessGrant.expires_at.desc())).first()


def access_status(user_id: int) -> dict[str, Any]:
    with db_session() as s:
        user = s.get(User, user_id)
        if user is not None and user.role == "admin":
            return {"active": True, "plan": "admin", "plan_name": "Admin",
                    "expires_at": None, "access": "unrestricted"}
        grant = _active_grant_in(s, user_id)
        if grant is None:
            return {"active": False, "plan": None, "plan_name": None, "expires_at": None, "access": None}
        return {"active": True, "plan": grant.plan,
                "plan_name": PLANS.get(grant.plan, {}).get("name", grant.plan),
                "expires_at": grant.expires_at, "access": "subscriber"}


def _extend_access(session, user_id: int, plan: str, days: int, granted_by: str,
                   payment_id: Optional[int], note: Optional[str]) -> AccessGrant:
    current = _active_grant_in(session, user_id)
    start = max(_now(), current.expires_at) if current else _now()   # stack on remaining time
    grant = AccessGrant(user_id=user_id, plan=plan, status="active", starts_at=start,
                        expires_at=start + days * 86400, granted_by=granted_by,
                        payment_id=payment_id, note=note)
    session.add(grant)
    session.flush()
    return grant


# ---------------------------------------------------------------------------
# operator decisions
# ---------------------------------------------------------------------------

def list_payments(status: Optional[str] = None) -> list[dict[str, Any]]:
    with db_session() as s:
        q = select(Payment, User).join(User, User.id == Payment.user_id)
        if status:
            q = q.where(Payment.status == status)
        rows = s.execute(q.order_by(Payment.created_at.desc())).all()
        out = []
        for p, u in rows:
            view = _view(p)
            view["user"] = {"id": u.id, "username": u.username, "email": u.email, "phone": u.phone}
            out.append(view)
        return out


def confirm_payment(payment_id: int, operator: str, note: str = "") -> dict[str, Any]:
    with db_session() as s:
        decided_at = _now()
        decision_note = (note or "").strip()[:300] or None
        # Compare-and-set first (before any read) so competing requests serialize
        # on this row and only one can transition submitted -> confirmed.
        result = s.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == "submitted")
            .values(status="confirmed", decided_at=decided_at,
                    decided_by=operator, decision_note=decision_note)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            current = s.get(Payment, payment_id)
            if current is None:
                raise LookupError("payment not found")
            raise ValueError(
                f"only submitted payments can be confirmed (this one is {current.status.replace('_', ' ')})"
            )

        p = s.get(Payment, payment_id)
        assert p is not None  # guarded by the update above
        _extend_access(s, p.user_id, p.plan, ACCESS_DAYS, operator, p.id, p.decision_note)
        s.flush()
        return _view(p)


def reject_payment(payment_id: int, operator: str, note: str) -> dict[str, Any]:
    note = (note or "").strip()
    if len(note) < 3:
        raise ValueError("give the reason for rejecting this payment")
    with db_session() as s:
        p = s.get(Payment, payment_id)
        if p is None:
            raise LookupError("payment not found")
        if p.status != "submitted":
            raise ValueError(f"only submitted payments can be rejected (this one is {p.status.replace('_', ' ')})")
        p.status = "rejected"
        p.decided_at = _now()
        p.decided_by = operator
        p.decision_note = note[:300]
        s.flush()
        return _view(p)


def grant_access(user_id: int, plan: str, days: int, operator: str, note: str) -> dict[str, Any]:
    """Manual grant without a payment record (e.g. a wire the operator checked offline)."""
    if plan not in PLANS:
        raise ValueError(f"unknown plan '{plan}'")
    if not (1 <= days <= 366):
        raise ValueError("days must be between 1 and 366")
    note = (note or "").strip()
    if len(note) < 3:
        raise ValueError("give a note explaining the manual grant")
    with db_session() as s:
        if s.get(User, user_id) is None:
            raise LookupError("user not found")
        grant = _extend_access(s, user_id, plan, days, operator, None, note[:300])
        return {"id": grant.id, "plan": plan, "expires_at": grant.expires_at}


def revoke_access(user_id: int, operator: str, note: str) -> int:
    note = (note or "").strip()
    if len(note) < 3:
        raise ValueError("give a note explaining the revocation")
    with db_session() as s:
        if s.get(User, user_id) is None:
            raise LookupError("user not found")
        rows = s.scalars(select(AccessGrant).where(
            AccessGrant.user_id == user_id, AccessGrant.status == "active")).all()
        for g in rows:
            g.status = "revoked"
            g.note = f"revoked by {operator}: {note[:200]}"
        return len(rows)


def list_users() -> list[dict[str, Any]]:
    with db_session() as s:
        users = s.scalars(select(User).order_by(User.created_at.desc())).all()
        out = []
        for u in users:
            grant = _active_grant_in(s, u.id)
            out.append({
                "id": u.id, "username": u.username, "email": u.email, "phone": u.phone,
                "role": u.role, "created_at": u.created_at,
                "access": {"active": grant is not None or u.role == "admin",
                           "plan": "admin" if u.role == "admin" else (grant.plan if grant else None),
                           "expires_at": grant.expires_at if grant else None},
            })
        return out
