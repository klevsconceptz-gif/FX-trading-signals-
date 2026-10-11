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
