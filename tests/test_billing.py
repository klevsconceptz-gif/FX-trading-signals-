"""Payments: invoices, reference submission, operator confirmation, paid access."""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fxbot import billing
from fxbot.auth import ACCOUNTS, issue_session
from fxbot.db import AccessGrant, Payment, db_session, reset_database
from fxbot.server import app

PW = "Str0ng!Pass99"
ADMIN = "Ops@test.fx"
ADMIN_PW = "Ops-Admin#2026"
WALLET = "bc1qexamplewalletaddress0001"


@pytest.fixture(autouse=True)
def fresh():
    reset_database()
    yield


def _client(token):
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def _user(name="buyer_one", email=None, phone="+15550200001"):
    user = ACCOUNTS.register(name, email or f"{name}@example.com", phone, PW)
    return user, _client(issue_session(user.id))


def _admin():
    admin = ACCOUNTS.seed_admin(ADMIN, ADMIN_PW)
    return admin, _client(issue_session(admin.id))


def _configure(admin_client, wire=True):
    body = {"crypto": [{"network": "BTC", "address": WALLET}], "wire": {}}
    if wire:
        body["wire"] = {"bank_name": "Example Bank", "account_name": "Klev FX Ltd",
                        "account_number": "0123456789", "routing_or_swift": "EXMPUS33",
                        "iban": "", "instructions": "Quote the invoice code."}
    r = admin_client.put("/api/admin/payment-settings", json=body)
    assert r.status_code == 200, r.text


# -- payment options ----------------------------------------------------------------

def test_payment_methods_unavailable_until_operator_configures_them():
    _, buyer = _user()
    opts = buyer.get("/api/payment-options").json()
    assert opts["methods"]["crypto"]["available"] is False
    assert opts["methods"]["wire"]["available"] is False
    r = buyer.post("/api/payments", json={"plan": "pro", "method": "crypto", "network": "BTC"})
    assert r.status_code == 422 and "not set up" in r.json()["detail"]
    r = buyer.post("/api/payments", json={"plan": "pro", "method": "wire"})
    assert r.status_code == 422 and "not set up" in r.json()["detail"]


def test_operator_sets_wallets_and_bank_details():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    opts = buyer.get("/api/payment-options").json()
    assert opts["methods"]["crypto"] == {"available": True, "networks": ["BTC"]}
    assert opts["methods"]["wire"]["available"] is True
    assert opts["plans"]["pro"]["price_usd"] == 79


def test_settings_validation():
    _, admin = _admin()
    r = admin.put("/api/admin/payment-settings", json={"crypto": [{"network": "BTC", "address": ""}]})
    assert r.status_code == 422
    r = admin.put("/api/admin/payment-settings", json={"crypto": [{"network": "bad net!", "address": WALLET}]})
    assert r.status_code == 422
    r = admin.put("/api/admin/payment-settings", json={"crypto": [{"network": "BTC", "address": "x y z"}]})
    assert r.status_code == 422
    r = admin.put("/api/admin/payment-settings", json={"wire": {"bank_name": "Only A Name"}})
    assert r.status_code == 422 and "account number" in r.json()["detail"]
    r = admin.put("/api/admin/payment-settings", json={"crypto": [
        {"network": "BTC", "address": WALLET}, {"network": "BTC", "address": WALLET + "2"}]})
    assert r.status_code == 422


# -- the payment lifecycle ------------------------------------------------------------

def test_full_crypto_lifecycle_grants_access():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()

    # 1. no access yet
    assert buyer.get("/api/universe").status_code == 403

    # 2. invoice created with the operator's wallet and an invoice code
    r = buyer.post("/api/payments", json={"plan": "pro", "method": "crypto", "network": "btc"})
    assert r.status_code == 200
    inv = r.json()["payment"]
    assert inv["invoice"].startswith("KFX-") and inv["amount_usd"] == 79.0
    assert inv["pay_to"] == {"network": "BTC", "address": WALLET}
    assert inv["status"] == "awaiting_payment"

    # 3. buyer submits the transaction hash
    r = buyer.post(f"/api/payments/{inv['id']}/reference",
                   json={"reference": "a1b2c3d4e5f6", "sender_note": "paid from exchange"})
    assert r.status_code == 200 and r.json()["payment"]["status"] == "submitted"
    assert buyer.get("/api/universe").status_code == 403           # still locked: not verified yet

    # 4. operator sees it in the queue and confirms after checking the wallet
    queue = admin.get("/api/admin/payments", params={"status": "submitted"}).json()["payments"]
    assert [p["invoice"] for p in queue] == [inv["invoice"]]
    assert queue[0]["user"]["username"] == "buyer_one"
    assert queue[0]["reference"] == "a1b2c3d4e5f6"
    r = admin.post(f"/api/admin/payments/{inv['id']}/confirm", json={"note": "received 0.0008 BTC"})
    assert r.status_code == 200 and r.json()["payment"]["status"] == "confirmed"
    assert r.json()["payment"]["decided_by"] == ADMIN

    # 5. access is unlocked, for 30 days
    me = buyer.get("/api/me").json()
    assert me["access"]["active"] is True and me["access"]["plan"] == "pro"
    assert buyer.get("/api/universe").status_code == 200
    remaining = me["access"]["expires_at"] - time.time()
    assert 29 * 86400 < remaining <= 30 * 86400


def test_wire_lifecycle_and_reference_must_be_submitted():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    inv = buyer.post("/api/payments", json={"plan": "starter", "method": "wire"}).json()["payment"]
    assert inv["pay_to"]["account_number"] == "0123456789"
    assert inv["amount_usd"] == 29.0
    assert admin.post(f"/api/admin/payments/{inv['id']}/confirm", json={}).status_code == 409   # not submitted
    assert buyer.post(f"/api/payments/{inv['id']}/reference", json={"reference": "ab"}).status_code == 422
    assert buyer.post(f"/api/payments/{inv['id']}/reference",
                      json={"reference": "WIRE-REF-778812"}).status_code == 200
    assert admin.post(f"/api/admin/payments/{inv['id']}/confirm", json={"note": "bank statement matched"}
                      ).status_code == 200


def test_rejected_payment_lets_buyer_try_again():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    inv = buyer.post("/api/payments", json={"plan": "pro", "method": "crypto", "network": "BTC"}).json()["payment"]
    buyer.post(f"/api/payments/{inv['id']}/reference", json={"reference": "deadbeef00"})
    assert admin.post(f"/api/admin/payments/{inv['id']}/reject", json={"note": ""}).status_code == 422
    r = admin.post(f"/api/admin/payments/{inv['id']}/reject", json={"note": "no funds at that address"})
    assert r.status_code == 200 and r.json()["payment"]["status"] == "rejected"
    assert buyer.get("/api/universe").status_code == 403
    again = buyer.post("/api/payments", json={"plan": "pro", "method": "crypto", "network": "BTC"})
    assert again.status_code == 200 and again.json()["payment"]["status"] == "awaiting_payment"


def test_one_submitted_payment_at_a_time_and_new_invoice_replaces_unpaid_one():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    first = buyer.post("/api/payments", json={"plan": "pro", "method": "crypto", "network": "BTC"}).json()["payment"]
    second = buyer.post("/api/payments", json={"plan": "elite", "method": "wire"}).json()["payment"]
    assert second["invoice"] != first["invoice"]
    with db_session() as s:
        assert s.get(Payment, first["id"]).status == "cancelled"
    buyer.post(f"/api/payments/{second['id']}/reference", json={"reference": "WIRE-000111"})
    r = buyer.post("/api/payments", json={"plan": "pro", "method": "wire"})
    assert r.status_code == 422 and "awaiting review" in r.json()["detail"]


def test_access_stacks_and_expires():
    _, admin = _admin()
    _configure(admin)
    user, buyer = _user()
    for ref in ("TX-AAAA1111", "TX-BBBB2222"):
        inv = buyer.post("/api/payments", json={"plan": "pro", "method": "wire"}).json()["payment"]
        buyer.post(f"/api/payments/{inv['id']}/reference", json={"reference": ref})
        admin.post(f"/api/admin/payments/{inv['id']}/confirm", json={"note": "ok"})
    days_left = (buyer.get("/api/me").json()["access"]["expires_at"] - time.time()) / 86400
    assert 58 < days_left <= 60                                  # two 30-day payments stack

    with db_session() as s:                                      # simulate expiry
        for g in s.scalars(select(AccessGrant).where(AccessGrant.user_id == user.id)):
            g.expires_at = time.time() - 1
    assert buyer.get("/api/universe").status_code == 403
    assert buyer.get("/api/me").json()["access"]["active"] is False


def test_buyer_cannot_touch_another_buyers_invoice():
    _, admin = _admin()
    _configure(admin)
    _, alice = _user("alice_pay", phone="+15550200101")
    _, bob = _user("bob_pay", phone="+15550200102")
    inv = alice.post("/api/payments", json={"plan": "pro", "method": "wire"}).json()["payment"]
    assert bob.post(f"/api/payments/{inv['id']}/reference", json={"reference": "STOLEN-REF"}).status_code == 404
    assert bob.get("/api/payments").json()["payments"] == []


def test_plan_admin_cannot_be_bought():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    assert buyer.post("/api/payments", json={"plan": "admin", "method": "wire"}).status_code == 422
    assert buyer.post("/api/payments", json={"plan": "pro", "method": "bitcoin-cash"}).status_code == 422


# -- operator controls ----------------------------------------------------------------

def test_manual_grant_and_revoke_with_notes():
    _, admin = _admin()
    user, buyer = _user()
    assert admin.post(f"/api/admin/users/{user.id}/grant",
                      json={"plan": "elite", "days": 7, "note": ""}).status_code == 422
    r = admin.post(f"/api/admin/users/{user.id}/grant",
                   json={"plan": "elite", "days": 7, "note": "wire confirmed by phone"})
    assert r.status_code == 200
    assert buyer.get("/api/universe").status_code == 200
    users = {u["username"]: u for u in admin.get("/api/admin/users").json()["users"]}
    assert users["buyer_one"]["access"]["active"] is True
    assert admin.post(f"/api/admin/users/{user.id}/revoke", json={"note": "chargeback"}).json()["revoked"] == 1
    assert buyer.get("/api/universe").status_code == 403


def test_only_operators_reach_admin_routes():
    _, buyer = _user()
    assert buyer.get("/api/admin/payments").status_code == 403
    assert buyer.get("/api/admin/users").status_code == 403
    assert buyer.put("/api/admin/payment-settings", json={}).status_code == 403
    anon = TestClient(app)
    assert anon.get("/api/admin/payments").status_code == 401


def test_admin_cannot_be_granted_by_buyer_or_plan_name():
    _, buyer = _user()
    assert buyer.post("/api/register", json={}).status_code == 422
    assert not hasattr(billing, "grant_admin")


def test_admin_identity_recorded_on_every_decision():
    _, admin = _admin()
    _configure(admin)
    _, buyer = _user()
    inv = buyer.post("/api/payments", json={"plan": "pro", "method": "wire"}).json()["payment"]
    buyer.post(f"/api/payments/{inv['id']}/reference", json={"reference": "WIRE-XYZ123"})
    admin.post(f"/api/admin/payments/{inv['id']}/confirm", json={"note": "ok"})
    with db_session() as s:
        grant = s.scalar(select(AccessGrant).where(AccessGrant.payment_id == inv["id"]))
        assert grant.granted_by == ADMIN
