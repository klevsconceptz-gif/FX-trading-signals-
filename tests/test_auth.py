"""Accounts, sessions, lockout and forgot-password (database-backed)."""

import secrets
import time

import pytest
from sqlalchemy import select

from fxbot import auth as auth_mod
from fxbot.auth import (
    ACCOUNTS,
    hash_password,
    issue_session,
    password_policy,
    password_strength,
    policy_error,
    verify_password,
)
from fxbot.db import User, UserSession, db_session, reset_database
from fxbot.server import app

ADMIN_USER = "Admin@test.fx"
# generated per run: the suite never carries a fixed admin password
ADMIN_PW = f"Tst-{secrets.token_hex(8)}#Aa9"
PW = "Str0ng!Pass99"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_database(monkeypatch):
    """Every test starts with an empty database and no dev reset codes."""
    monkeypatch.delenv("KLEVS_DEV_RESET_CODES", raising=False)
    monkeypatch.delenv("KLEVS_EMAIL_URL", raising=False)
    reset_database()
    yield


def _register(client, username="trader_one", email="trader@example.com", phone="+15550002222", pw=PW):
    return client.post("/api/register", json={
        "username": username, "email": email, "phone": phone, "password": pw})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


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


# -- registration ---------------------------------------------------------------

def test_register_creates_pending_user_and_session(client):
    r = _register(client)
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["user"]["role"] == "user"
    assert body["access"]["active"] is False            # no access until payment confirmed
    with db_session() as s:
        stored = s.scalar(select(User).where(User.username == "trader_one"))
        assert stored.password_hash != PW and verify_password(PW, stored.password_hash)


def test_session_token_is_stored_only_as_hash(client):
    token = _register(client).json()["token"]
    with db_session() as s:
        hashes = [row.token_hash for row in s.scalars(select(UserSession))]
    assert hashes and token not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_register_rejects_weak_password(client):
    r = _register(client, pw="Sh0rt!Xy")
    assert r.status_code == 422 and "at least 10" in r.json()["detail"]


def test_register_rejects_duplicates_and_bad_fields(client):
    assert _register(client).status_code == 200
    assert _register(client, username="trader_one", email="other@example.com",
                     phone="+15550003333").status_code == 422          # username taken
    assert _register(client, username="other_user", email="trader@example.com",
                     phone="+15550004444").status_code == 422          # email taken
    assert _register(client, username="third_user", email="third@example.com",
                     phone="+15550002222").status_code == 422          # phone taken
    assert _register(client, username="ab").status_code == 422
    assert _register(client, username="bad_mail", email="nope").status_code == 422
    assert _register(client, username="bad_phone", phone="abc").status_code == 422


# -- login ----------------------------------------------------------------------

def test_login_with_all_three_identifiers(client):
    _register(client)
    for ident in ("trader_one", "trader@example.com", "+1 555 000 2222", "+15550002222"):
        r = client.post("/api/auth/login", json={"identifier": ident, "password": PW})
        assert r.status_code == 200, ident
        assert r.json()["user"]["username"] == "trader_one"


def test_login_failures_are_indistinguishable(client):
    _register(client)
    wrong_pw = client.post("/api/auth/login", json={"identifier": "trader_one", "password": "Wrong!Pass99"})
    unknown = client.post("/api/auth/login", json={"identifier": "nobody_here", "password": "Wrong!Pass99"})
    assert wrong_pw.status_code == 401 and unknown.status_code == 401
    assert wrong_pw.json()["detail"] == unknown.json()["detail"]    # no account enumeration


def test_repeated_wrong_passwords_lock_the_account(client):
    _register(client)
    for _ in range(auth_mod.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json={"identifier": "trader_one", "password": "Wrong!Pass99"})
    r = client.post("/api/auth/login", json={"identifier": "trader_one", "password": PW})
    assert r.status_code == 429                                     # correct password, still locked
    with db_session() as s:
        user = s.scalar(select(User).where(User.username == "trader_one"))
        user.locked_until = time.time() - 1                         # lock window has passed
    assert client.post("/api/auth/login", json={"identifier": "trader_one", "password": PW}).status_code == 200


def test_logout_revokes_the_token(client):
    token = _register(client).json()["token"]
    assert client.get("/api/me", headers=_bearer(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 200
    assert client.get("/api/me", headers=_bearer(token)).status_code == 401


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=_bearer("made-up-token")).status_code == 401


def test_expired_session_is_rejected(client):
    _register(client)
    with db_session() as s:
        user = s.scalar(select(User).where(User.username == "trader_one"))
        token = issue_session(user.id)
    with db_session() as s:
        row = s.get(UserSession, auth_mod._token_hash(token))
        row.expires_at = time.time() - 1
    assert client.get("/api/me", headers=_bearer(token)).status_code == 401


# -- forgot / reset -------------------------------------------------------------

def test_forgot_never_reveals_whether_an_account_exists(client):
    _register(client)
    known = client.post("/api/auth/forgot", json={"email": "trader@example.com"})
    unknown = client.post("/api/auth/forgot", json={"email": "nobody@none.co"})
    assert known.status_code == 200 and unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_reset_code_is_never_returned_by_default(client):
    _register(client)
    body = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()
    assert "dev_code" not in body
    assert "delivered" not in body                                 # delivery status is never exposed


def test_reset_code_stored_only_as_hash(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    code = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    with db_session() as s:
        user = s.scalar(select(User).where(User.username == "trader_one"))
        assert user.reset_hash and code not in user.reset_hash


def test_forgot_then_reset_full_chain(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    code = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    new_pw = "Brand-New9Pass"
    r = client.post("/api/auth/reset", json={"email": "trader@example.com", "code": code, "new_password": new_pw})
    assert r.status_code == 200 and r.json()["token"]
    assert client.post("/api/auth/login", json={"identifier": "trader_one", "password": PW}).status_code == 401
    assert client.post("/api/auth/login", json={"identifier": "trader_one", "password": new_pw}).status_code == 200


def test_reset_rejects_wrong_code_and_locks_after_five(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    real = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    wrong = "0000000" if real != "0000000" else "1111111"
    for _ in range(auth_mod.RESET_MAX_ATTEMPTS):
        r = client.post("/api/auth/reset", json={"email": "trader@example.com", "code": wrong,
                                                 "new_password": "Brand-New9Pass"})
        assert r.status_code == 422
    r = client.post("/api/auth/reset", json={"email": "trader@example.com", "code": real,
                                             "new_password": "Brand-New9Pass"})
    # locked out: even the correct code is refused, with the same generic message as any failure
    assert r.status_code == 422 and "request a new code" in r.json()["detail"]


def test_reset_code_expires(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    code = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    with db_session() as s:
        user = s.scalar(select(User).where(User.username == "trader_one"))
        user.reset_expires = time.time() - 1
    r = client.post("/api/auth/reset", json={"email": "trader@example.com", "code": code,
                                             "new_password": "Brand-New9Pass"})
    assert r.status_code == 422 and "expired" in r.json()["detail"]


def test_reset_rejects_weak_new_password(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    code = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    r = client.post("/api/auth/reset", json={"email": "trader@example.com", "code": code, "new_password": "weak"})
    assert r.status_code == 422


def test_reset_code_is_sent_through_the_mail_relay(client, monkeypatch):
    _register(client)
    sent = []
    monkeypatch.setattr(auth_mod.mail, "send", lambda to, subject, text: sent.append((to, subject, text)) or True)
    body = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()
    assert "delivered" not in body and "dev_code" not in body
    assert len(sent) == 1
    to, _subject, text = sent[0]
    assert to == "trader@example.com"
    assert "reset code is " in text


# -- admin account --------------------------------------------------------------

def test_admin_password_meets_policy():
    assert password_policy(ADMIN_PW)["valid"] is True


def test_seed_admin_creates_admin_and_rotates_password():
    acc = ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    assert acc.role == "admin" and acc.email == "admin@test.fx"
    assert ACCOUNTS.authenticate(ADMIN_USER, ADMIN_PW).role == "admin"
    assert ACCOUNTS.authenticate("admin@test.fx", ADMIN_PW).role == "admin"
    ACCOUNTS.seed_admin(ADMIN_USER, "Rotated-Pass9#")
    with pytest.raises(PermissionError):
        ACCOUNTS.authenticate(ADMIN_USER, ADMIN_PW)
    with db_session() as s:
        assert len(s.scalars(select(User).where(User.role == "admin")).all()) == 1


def test_seed_admin_password_rotation_revokes_existing_sessions():
    admin = ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    old_token = issue_session(admin.id)
    assert auth_mod.resolve_session(old_token) is not None

    ACCOUNTS.seed_admin(ADMIN_USER, "Rotated-Pass9#")
    assert auth_mod.resolve_session(old_token) is None

    current_token = issue_session(admin.id)
    # A restart with the same authoritative password should not log out everyone.
    ACCOUNTS.seed_admin(ADMIN_USER, "Rotated-Pass9#")
    assert auth_mod.resolve_session(current_token) is not None


def test_seed_admin_rejects_weak_password_and_bad_fields():
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin(ADMIN_USER, "weakpass")
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin("has space@klev.fx", ADMIN_PW)
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin("notanemail", ADMIN_PW)


def test_seed_admin_refuses_to_take_over_a_user_account():
    ACCOUNTS.register("victim_user", "admin@test.fx", "+15550003333", ADMIN_PW)
    with pytest.raises(ValueError):
        ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)


def test_admin_login_is_unrestricted(client):
    ACCOUNTS.seed_admin(ADMIN_USER, ADMIN_PW)
    r = client.post("/api/auth/login", json={"identifier": ADMIN_USER, "password": ADMIN_PW})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["access"]["active"] is True and body["access"]["access"] == "unrestricted"
    assert client.get("/api/universe", headers=_bearer(body["token"])).status_code == 200


def test_bootstrap_from_env(monkeypatch):
    from fxbot import server

    monkeypatch.delenv("KFX_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("KFX_ADMIN_PASSWORD", raising=False)
    assert server._bootstrap_admin_from_env() is None
    monkeypatch.setenv("KFX_ADMIN_USERNAME", ADMIN_USER)
    monkeypatch.setenv("KFX_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("KFX_ADMIN_PHONE", "+15559990000")
    assert server._bootstrap_admin_from_env() == ADMIN_USER
    assert ACCOUNTS.authenticate("+1 555 999 0000", ADMIN_PW).role == "admin"
    monkeypatch.delenv("KFX_ADMIN_PASSWORD")
    with pytest.raises(RuntimeError):
        server._bootstrap_admin_from_env()


def test_password_policy_endpoint(client):
    body = client.get("/api/auth/password-policy").json()
    assert body["min_length"] == 10
    assert set(body["rules"]) == {"length_10", "uppercase", "lowercase", "number", "symbol"}


def test_removed_insecure_routes(client):
    assert client.post("/api/login", json={"email": "trader@example.com"}).status_code in (404, 405)
    assert client.post("/api/subscribe", json={"name": "x", "email": "a@b.co", "plan": "pro"}).status_code in (404, 405)


# -- enumeration and session-revocation regressions (round 11 system check) -------

def test_forgot_response_identical_for_known_and_unknown_email(client):
    _register(client)
    known = client.post("/api/auth/forgot", json={"email": "trader@example.com"})
    unknown = client.post("/api/auth/forgot", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()          # no 'delivered' or other account-dependent field


def test_reset_failures_share_one_message(client):
    _register(client)
    no_pending = client.post("/api/auth/reset", json={
        "email": "ghost@example.com", "code": "1234567", "new_password": "Brand-New9Pass"})
    assert no_pending.status_code == 422
    assert not no_pending.json()["detail"].startswith("'")          # no KeyError repr quoting
    client.post("/api/auth/forgot", json={"email": "trader@example.com"})   # pending reset, wrong code
    wrong = client.post("/api/auth/reset", json={
        "email": "trader@example.com", "code": "0000000", "new_password": "Brand-New9Pass"})
    assert wrong.status_code == 422
    assert wrong.json() == no_pending.json()


def test_password_reset_revokes_existing_sessions(client, monkeypatch):
    _register(client)
    old = client.post("/api/auth/login", json={"identifier": "trader_one", "password": PW}).json()["token"]
    assert client.get("/api/me", headers=_bearer(old)).status_code == 200
    monkeypatch.setenv("KLEVS_DEV_RESET_CODES", "1")
    code = client.post("/api/auth/forgot", json={"email": "trader@example.com"}).json()["dev_code"]
    r = client.post("/api/auth/reset", json={
        "email": "trader@example.com", "code": code, "new_password": "Brand-New9Pass"})
    assert r.status_code == 200
    assert client.get("/api/me", headers=_bearer(old)).status_code == 401   # old token is dead
    assert client.get("/api/me", headers=_bearer(r.json()["token"])).status_code == 200
