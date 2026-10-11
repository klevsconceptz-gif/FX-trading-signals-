"""Registration, credential login and forgot-password (hashed email auth) tests."""

import time

import pytest

from fxbot.auth import (
    ACCOUNTS,
    hash_password,
    password_policy,
    password_strength,
    policy_error,
    verify_password,
)
from fxbot.server import app


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_accounts():
    """Isolate account store between tests."""
    for store in (ACCOUNTS._by_username, ACCOUNTS._by_email, ACCOUNTS._by_phone):
        store.clear()
    yield
    for store in (ACCOUNTS._by_username, ACCOUNTS._by_email, ACCOUNTS._by_phone):
        store.clear()


REG = {
    "username": "ada_fx",
    "email": "ada@example.com",
    "phone": "+1 555 123 4567",
    "password": "Str0ng!Pass99",
    "plan": "pro",
}


# ---------------------------------------------------------------- password policy

def test_password_hash_roundtrip():
    digest = hash_password("Str0ng!Pass99")
    assert digest.count(":") == 1          # salt:digest hex storage
    assert verify_password("Str0ng!Pass99", digest)
    assert not verify_password("wrong!Pass99", digest)


def test_password_policy_cases():
    cases = {
        "Sh0rt!Xy": False,          # 8 chars — too short
        "alllower1!xx": False,      # no uppercase
        "ALLUPPER1!XX": False,      # no lowercase
        "NoNumbers!!": False,       # no number
        "NoSymbols123": False,      # no symbol
        "Str0ng!Pass99": True,      # compliant
        "Aa1!" + "x" * 6: True,    # exactly 10 chars
    }
    for pw, ok in cases.items():
        checks = password_policy(pw)["checks"]
        assert all(checks.values()) is ok, (pw, checks)
        assert (policy_error(pw) is None) is ok, pw


def test_password_policy_error_messages():
    assert policy_error("short") is not None
    assert "10" in policy_error("short")          # mentions minimum length
    assert policy_error("Str0ng!Pass99") is None


def test_password_strength_meter():
    assert password_strength("") == "none"
    assert password_strength("abc") == "weak"
    assert password_strength("Str0ng!Pass99") in ("strong", "very-strong")


# ---------------------------------------------------------------- registration

def test_register_creates_account_and_session(client):
    r = client.post("/api/register", json=REG)
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["subscription"]["plan"] == "pro"
    assert body["subscription"]["username"] == "ada_fx"
    assert client.get(f"/api/subscription/{body['token']}").status_code == 200


def test_register_rejects_weak_password(client):
    bad = dict(REG, password="weakpass")
    r = client.post("/api/register", json=bad)
    assert r.status_code == 422
    assert "password" in str(r.json()["detail"]).lower()


def test_register_rejects_duplicate_credentials(client):
    assert client.post("/api/register", json=REG).status_code == 200
    assert client.post("/api/register", json=dict(REG)).status_code == 422
    assert client.post("/api/register", json=dict(
        REG, username="other_user")).status_code == 422          # email taken
    assert client.post("/api/register", json=dict(
        REG, username="other_user", email="o@e.com")).status_code == 422  # phone taken


def test_register_rejects_bad_identity_fields(client):
    assert client.post("/api/register", json=dict(REG, username="x!")).status_code in (404, 422)
    assert client.post("/api/register", json=dict(
        REG, username="has space")).status_code in (404, 422)
    assert client.post("/api/register", json=dict(REG, phone="12")).status_code == 422
    assert client.post("/api/register", json=dict(REG, plan="gold")).status_code == 422


# ---------------------------------------------------------------- login

def test_login_with_all_three_identifiers(client):
    assert client.post("/api/register", json=REG).status_code == 200
    for ident in ("ada_fx", "ada@example.com", "+1 555 123 4567", "+15551234567"):
        r = client.post("/api/auth/login",
                        json={"identifier": ident, "password": "Str0ng!Pass99"})
        assert r.status_code == 200, ident
        assert r.json()["token"]


def test_login_wrong_password_401(client):
    client.post("/api/register", json=REG)
    r = client.post("/api/auth/login",
                    json={"identifier": "ada_fx", "password": "Wrong!Pass99"})
    assert r.status_code == 401


def test_login_unknown_identifier_404(client):
    r = client.post("/api/auth/login",
                    json={"identifier": "ghost", "password": "Str0ng!Pass99"})
    assert r.status_code == 404


def test_login_reissues_token_and_retires_old(client):
    tok1 = client.post("/api/register", json=REG).json()["token"]
    r = client.post("/api/auth/login",
                    json={"identifier": "ada_fx", "password": "Str0ng!Pass99"})
    tok2 = r.json()["token"]
    assert tok1 != tok2
    assert client.get(f"/api/subscription/{tok2}").status_code == 200
    assert client.get(f"/api/subscription/{tok1}").status_code == 404


# ---------------------------------------------------------------- forgot / reset

def test_forgot_password_returns_code_and_hides_it_in_store(client):
    client.post("/api/register", json=REG)
    r = client.post("/api/auth/forgot", json={"email": "ada@example.com"})
    assert r.status_code == 200
    code = r.json()["dev_code"]
    assert len(code) == 7 and code.isdigit()
    # only a hash of the code is stored on the account
    acc = ACCOUNTS._by_email["ada@example.com"]
    assert acc.reset_hash and ":" in acc.reset_hash
    assert code not in acc.reset_hash


def test_forgot_unknown_email_404(client):
    assert client.post("/api/auth/forgot",
                       json={"email": "ghost@example.com"}).status_code == 404


def test_reset_password_full_chain(client):
    client.post("/api/register", json=REG)
    code = client.post("/api/auth/forgot", json={"email": "ada@example.com"}).json()["dev_code"]

    # wrong code first
    r = client.post("/api/auth/reset", json={
        "email": "ada@example.com", "code": "0000000", "new_password": "N3w!Passw0rd"})
    assert r.status_code == 422

    # correct code + policy-compliant new password
    r = client.post("/api/auth/reset", json={
        "email": "ada@example.com", "code": code, "new_password": "N3w!Passw0rd"})
    assert r.status_code == 200
    assert r.json()["token"]

    # old password no longer works; new one logs in
    assert client.post("/api/auth/login", json={
        "identifier": "ada_fx", "password": "Str0ng!Pass99"}).status_code == 401
    assert client.post("/api/auth/login", json={
        "identifier": "ada@example.com", "password": "N3w!Passw0rd"}).status_code == 200


def test_reset_rejects_weak_new_password(client):
    client.post("/api/register", json=REG)
    code = client.post("/api/auth/forgot", json={"email": "ada@example.com"}).json()["dev_code"]
    r = client.post("/api/auth/reset", json={
        "email": "ada@example.com", "code": code, "new_password": "weakpass"})
    assert r.status_code == 422


def test_reset_code_expires(client):
    client.post("/api/register", json=REG)
    code = client.post("/api/auth/forgot", json={"email": "ada@example.com"}).json()["dev_code"]
    ACCOUNTS._by_email["ada@example.com"].reset_expires = time.time() - 1   # 15-min TTL passed
    r = client.post("/api/auth/reset", json={
        "email": "ada@example.com", "code": code, "new_password": "N3w!Passw0rd"})
    assert r.status_code == 422
    assert "expired" in str(r.json()["detail"]).lower()


def test_reset_code_lockout_after_five_attempts(client):
    client.post("/api/register", json=REG)
    code = client.post("/api/auth/forgot", json={"email": "ada@example.com"}).json()["dev_code"]
    for _ in range(5):
        r = client.post("/api/auth/reset", json={
            "email": "ada@example.com", "code": "1111111", "new_password": "N3w!Passw0rd"})
        assert r.status_code == 422
    # even the right code is now refused
    r = client.post("/api/auth/reset", json={
        "email": "ada@example.com", "code": code, "new_password": "N3w!Passw0rd"})
    assert r.status_code == 422
    assert "attempt" in str(r.json()["detail"]).lower() or "expired" in str(r.json()["detail"]).lower()


def test_password_policy_endpoint(client):
    r = client.get("/api/auth/password-policy")
    assert r.status_code == 200
    body = r.json()
    assert body["min_length"] == 10
    assert set(body["rules"]) == {"length_10", "uppercase", "lowercase", "number", "symbol"}


# ---------------------------------------------------------------------------
# admin account (operator-provisioned, unrestricted access)
# ---------------------------------------------------------------------------

# test-only credentials (the real admin password is supplied via KFX_ADMIN_* env vars)
ADMIN_USER = "Admin@test.fx"
ADMIN_PW = "Test-Admin#2026"


def test_admin_password_meets_policy():
    assert password_policy(ADMIN_PW)["valid"] is True


def test_seed_admin_creates_unrestricted_admin_and_hashes_password():
    acc = ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    assert acc.role == "admin" and acc.plan == "admin"
    assert acc.email == "admin@test.fx"          # defaults to the address username
    assert ADMIN_PW not in acc.password_hash     # never stored in plain text
    assert ACCOUNTS.authenticate(ADMIN_USER, ADMIN_PW).role == "admin"
    assert ACCOUNTS.authenticate("admin@test.fx", ADMIN_PW).role == "admin"


def test_admin_login_grants_unrestricted_session(client):
    ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    r = client.post("/api/auth/login", json={"identifier": ADMIN_USER, "password": ADMIN_PW})
    assert r.status_code == 200
    sub = r.json()["subscription"]
    assert sub["role"] == "admin"
    assert sub["plan"] == "admin" and sub["plan_name"] == "Admin"
    assert sub["access"] == "unrestricted"
    assert sub["status"] == "active" and sub["price_usd"] == 0
    # the session token unlocks the same subscription check the paywall uses
    token = r.json()["token"]
    assert client.get(f"/api/subscription/{token}").status_code == 200


def test_admin_wrong_password_rejected(client):
    ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    r = client.post("/api/auth/login", json={"identifier": ADMIN_USER, "password": "wrong-Pass1!"})
    assert r.status_code == 401


def test_admin_plan_cannot_be_self_selected(client):
    r = client.post("/api/register", json={
        "username": "sneaky_user", "email": "sneaky@example.com", "phone": "+15550001111",
        "password": ADMIN_PW, "plan": "admin"})
    assert r.status_code == 422
    r = client.post("/api/subscribe", json={"name": "x", "email": "x@example.com", "plan": "admin"})
    assert r.status_code == 422


def test_normal_registration_is_never_admin(client):
    r = client.post("/api/register", json={
        "username": "trader_one", "email": "trader@example.com", "phone": "+15550002222",
        "password": ADMIN_PW, "plan": "pro"})
    assert r.status_code == 200
    sub = r.json()["subscription"]
    assert sub["role"] == "user" and "access" not in sub


def test_seed_admin_rotates_password_in_place():
    ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    ACCOUNTS.seed_admin(ADMIN_USER, "Rotated-Pass9#")
    assert ACCOUNTS.authenticate(ADMIN_USER, "Rotated-Pass9#").role == "admin"
    with pytest.raises(PermissionError):
        ACCOUNTS.authenticate(ADMIN_USER, ADMIN_PW)
    assert len(ACCOUNTS._by_username) == 1


def test_seed_admin_rejects_weak_password_and_bad_fields():
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin(ADMIN_USER, "weakpass")
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin("has space@klev.fx", ADMIN_PW)
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin("notanemail", ADMIN_PW)   # no email derivable


def test_seed_admin_refuses_to_take_over_a_user_account():
    ACCOUNTS.register("victim_user", "admin@test.fx", "+15550003333", ADMIN_PW)
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)


def test_bootstrap_from_env(monkeypatch):
    from fxbot import server

    monkeypatch.delenv("KFX_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("KFX_ADMIN_PASSWORD", raising=False)
    assert server._bootstrap_admin_from_env() is None          # no-op when unset

    monkeypatch.setenv("KFX_ADMIN_USERNAME", ADMIN_USER)
    monkeypatch.setenv("KFX_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("KFX_ADMIN_PHONE", "+15559990000")
    assert server._bootstrap_admin_from_env() == ADMIN_USER
    assert ACCOUNTS.authenticate("+1 555 999 0000", ADMIN_PW).role == "admin"

    monkeypatch.delenv("KFX_ADMIN_PASSWORD")
    with pytest.raises(RuntimeError):                           # half-configured -> refuse
        server._bootstrap_admin_from_env()
