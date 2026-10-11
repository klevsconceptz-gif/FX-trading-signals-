"""Account system: registration, credential login, sessions, forgot-password.

Security model (stdlib hashing; database via SQLAlchemy — see ``fxbot/db.py``):

* Passwords are never stored in plain text: PBKDF2-HMAC-SHA256 (200k iterations)
  with a unique random salt, compared in constant time.
* Login accepts username, email or phone + password.  Ten wrong passwords lock
  the account for 15 minutes.
* Session tokens are random 256-bit values; the database holds only their
  SHA-256 hash, so a leaked table cannot be replayed.
* Reset codes are 7-digit, stored hashed, expire after 15 minutes and lock
  after 5 wrong attempts.  The code is e-mailed (SMTP, see ``deliver_reset_code``);
  it is never returned in an API response unless ``KLEVS_DEV_RESET_CODES=1``
  (local development only).

Password policy: minimum 10 characters with an uppercase letter, a lowercase
letter, a number and a symbol.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from typing import Any, Optional

from sqlalchemy import delete, or_, select

from .db import User, UserSession, db_session

log = logging.getLogger("klevs.auth")

PBKDF2_ITERATIONS = 200_000
RESET_CODE_TTL = 15 * 60          # seconds
RESET_MAX_ATTEMPTS = 5            # wrong-code attempts before invalidation
RESET_FAILED = "reset code is incorrect or has expired — request a new code"  # the only reset error shown to callers
MIN_PASSWORD_LEN = 10
LOGIN_MAX_FAILURES = 10           # wrong passwords before a temporary lock
LOGIN_LOCK_SECONDS = 15 * 60
SESSION_TTL = 14 * 86400          # bearer tokens last two weeks

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-().]{6,19}$")


class LockedError(PermissionError):
    """Too many failed logins: the account is temporarily locked."""


# ---------------------------------------------------------------------------
# password hashing & policy
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """salt:hex PBKDF2-HMAC-SHA256 digest — safe to store."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate, expected)


def password_policy(password: str) -> dict[str, Any]:
    """Return the checklist + overall validity for a candidate password."""
    checks = {
        "length_10": len(password) >= MIN_PASSWORD_LEN,
        "uppercase": bool(re.search(r"[A-Z]", password)),
        "lowercase": bool(re.search(r"[a-z]", password)),
        "number": bool(re.search(r"[0-9]", password)),
        "symbol": bool(re.search(r"[^A-Za-z0-9]", password)),
    }
    return {"valid": all(checks.values()), "checks": checks, "min_length": MIN_PASSWORD_LEN}


def password_strength(password: str) -> str:
    """Coarse strength label for the UI meter."""
    p = password_policy(password)
    if not password:
        return "none"
    score = sum(p["checks"].values())
    if score <= 2:
        return "weak"
    if score == 3:
        return "fair"
    if score == 4:
        return "strong"
    return "very-strong"


def policy_error(password: str) -> Optional[str]:
    p = password_policy(password)
    if p["valid"]:
        return None
    missing = [name for name, ok in p["checks"].items() if not ok]
    pretty = {
        "length_10": f"at least {MIN_PASSWORD_LEN} characters",
        "uppercase": "an uppercase letter (A-Z)",
        "lowercase": "a lowercase letter (a-z)",
        "number": "a number (0-9)",
        "symbol": "a symbol (!@#$%…)",
    }
    return "password must include " + ", ".join(pretty[m] for m in missing)


def _digits(value: str) -> str:
    return re.sub(r"[^\d+]", "", value)


def _code_hash(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 50_000).hex()


# ---------------------------------------------------------------------------
# account store
# ---------------------------------------------------------------------------

class AccountStore:
    """Database-backed account registry."""

    # -- registration --------------------------------------------------------

    def register(self, username: str, email: str, phone: str, password: str) -> User:
        username, email, phone = username.strip(), email.strip().lower(), phone.strip()
        if not USERNAME_RE.match(username):
            raise ValueError("username must be 3-32 characters: letters, numbers, _ . - only")
        if not EMAIL_RE.match(email):
            raise ValueError("invalid email address")
        if not PHONE_RE.match(phone):
            raise ValueError("invalid phone number (digits, +, -, spaces, parentheses; 7-20 chars)")
        err = policy_error(password)
        if err:
            raise ValueError(err)

        with db_session() as s:
            if s.scalar(select(User.id).where(User.username_key == username.lower())):
                raise ValueError("username is already taken")
            if s.scalar(select(User.id).where(User.email == email)):
                raise ValueError("an account with that email already exists")
            if s.scalar(select(User.id).where(User.phone == phone)):
                raise ValueError("an account with that phone number already exists")
            user = User(username=username, username_key=username.lower(), email=email,
                        phone=phone, password_hash=hash_password(password),
                        role="user", created_at=time.time())
            s.add(user)
            s.flush()
            return user

    # -- login ---------------------------------------------------------------

    def authenticate(self, identifier: str, password: str) -> User:
        ident = identifier.strip()
        with db_session() as s:
            user = s.scalar(select(User).where(or_(
                User.username == ident,
                User.email == ident.lower(),
                User.phone == ident,
            )))
            if user is None and re.fullmatch(r"[0-9+\s\-().]+", ident):
                norm = _digits(ident)
                for candidate in s.scalars(select(User).where(User.phone.is_not(None))):
                    if _digits(candidate.phone or "") == norm:
                        user = candidate
                        break
            if user is None:
                raise ValueError("no account found for that username, email or phone")

            now = time.time()
            if user.locked_until > now:
                minutes = max(1, int((user.locked_until - now) // 60) + 1)
                raise LockedError(f"too many failed attempts — try again in about {minutes} minutes")
            ok = verify_password(password, user.password_hash)
            if ok:
                user.failed_logins = 0
                user.locked_until = 0.0
            else:
                user.failed_logins += 1
                if user.failed_logins >= LOGIN_MAX_FAILURES:
                    user.locked_until = now + LOGIN_LOCK_SECONDS
                    user.failed_logins = 0
            # the transaction commits when this block exits — raise only after it
        if not ok:
            raise PermissionError("incorrect password")
        return user

    def get(self, user_id: int) -> Optional[User]:
        with db_session() as s:
            return s.get(User, user_id)

    # -- forgot / reset password (email code) --------------------------------

    def issue_reset(self, email: str) -> str:
        """Create a one-time reset code, store ONLY its hash, return the raw code
        for delivery.  Raises KeyError when no account has that email."""
        with db_session() as s:
            user = s.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None:
                raise KeyError("no account found for that email")
            code = f"{secrets.randbelow(10_000_000):07d}"
            salt = secrets.token_bytes(8)
            user.reset_hash = salt.hex() + ":" + _code_hash(code, salt)
            user.reset_expires = time.time() + RESET_CODE_TTL
            user.reset_attempts = 0
        return code

    def reset_password(self, email: str, code: str, new_password: str) -> User:
        err = policy_error(new_password)
        if err:
            raise ValueError(err)
        outcome: Optional[str] = None
        with db_session() as s:
            user = s.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None or user.reset_hash is None:
                # same wording as every other failure: do not reveal which emails have a pending reset
                raise ValueError(RESET_FAILED)
            if time.time() > user.reset_expires:
                user.reset_hash = None
                outcome = "expired"
            elif user.reset_attempts >= RESET_MAX_ATTEMPTS:
                user.reset_hash = None
                outcome = "locked"
            else:
                salt_hex, digest_hex = user.reset_hash.split(":", 1)
                candidate = _code_hash(code, bytes.fromhex(salt_hex))
                if not hmac.compare_digest(candidate, digest_hex):
                    user.reset_attempts += 1
                    outcome = "wrong"
                else:
                    user.password_hash = hash_password(new_password)
                    user.reset_hash = None
                    user.reset_attempts = 0
                    user.failed_logins = 0
                    user.locked_until = 0.0
                    # a password reset ends every existing session for the account
                    s.execute(delete(UserSession).where(UserSession.user_id == user.id))
                    outcome = "ok"
        if outcome in ("expired", "locked", "wrong"):
            # one message for every failure, so the response never shows account state
            raise ValueError(RESET_FAILED)
        return user

    # -- operator-provisioned admin -------------------------------------------

    def seed_admin(self, username: str, password: str,
                   email: Optional[str] = None, phone: Optional[str] = None) -> User:
        """Create or rotate the admin account.

        Admin usernames are not restricted to the user-facing rule (so an address
        such as ``Admin@klev.fx`` works); the password policy still applies.
        Email defaults to the username when that is an address.  Calling again
        with the same username rotates the password in place.
        """
        username = username.strip()
        if not username or len(username) > 64 or any(c.isspace() for c in username):
            raise ValueError("admin username must be 1-64 characters with no spaces")
        email = (email if email is not None else username).strip().lower()
        if not EMAIL_RE.match(email):
            raise ValueError("admin email must be a valid address (set KFX_ADMIN_EMAIL)")
        phone = (phone or "").strip()
        if phone and not PHONE_RE.match(phone):
            raise ValueError("invalid admin phone number")
        err = policy_error(password)
        if err:
            raise ValueError(err)

        with db_session() as s:
            by_name = s.scalar(select(User).where(User.username_key == username.lower()))
            by_mail = s.scalar(select(User).where(User.email == email))
            for hit in (by_name, by_mail):
                if hit is not None and hit.role != "admin":
                    raise ValueError("admin credentials clash with an existing user account")
            user = by_name or by_mail
            if user is None:
                user = User(username=username, username_key=username.lower(), email=email,
                            phone=phone or None, password_hash=hash_password(password),
                            role="admin", created_at=time.time())
                s.add(user)
            else:
                user.password_hash = hash_password(password)
                user.role = "admin"
                user.reset_hash, user.reset_attempts = None, 0
                user.failed_logins, user.locked_until = 0, 0.0
                if phone:
                    user.phone = phone
            s.flush()
            return user


ACCOUNTS = AccountStore()


# ---------------------------------------------------------------------------
# sessions (bearer tokens; only hashes are stored)
# ---------------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db_session() as s:
        s.add(UserSession(token_hash=_token_hash(token), user_id=user_id,
                          created_at=now, expires_at=now + SESSION_TTL))
    return token


def resolve_session(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    with db_session() as s:
        row = s.get(UserSession, _token_hash(token))
        if row is None:
            return None
        if row.expires_at < time.time():
            s.delete(row)
            return None
        return s.get(User, row.user_id)


def revoke_session(token: Optional[str]) -> None:
    if not token:
        return
    with db_session() as s:
        row = s.get(UserSession, _token_hash(token))
        if row is not None:
            s.delete(row)


# ---------------------------------------------------------------------------
# reset-code delivery
# ---------------------------------------------------------------------------

def dev_reset_codes_enabled() -> bool:
    return os.environ.get("KLEVS_DEV_RESET_CODES", "").strip().lower() in ("1", "true", "yes")


def deliver_reset_code(email: str, code: str) -> bool:
    """E-mail a reset code over SMTP.  Returns False when SMTP is not configured
    (the code is then never shown to the user — the reset simply cannot finish).

    Environment: KLEVS_SMTP_HOST, KLEVS_SMTP_PORT (587), KLEVS_SMTP_USER,
    KLEVS_SMTP_PASSWORD, KLEVS_SMTP_FROM.  STARTTLS is required.
    """
    host = os.environ.get("KLEVS_SMTP_HOST", "").strip()
    sender = os.environ.get("KLEVS_SMTP_FROM", "").strip()
    if not host or not sender:
        log.warning("reset code not sent: SMTP is not configured (KLEVS_SMTP_HOST / KLEVS_SMTP_FROM)")
        return False
    msg = EmailMessage()
    msg["Subject"] = "klevs FX trading App — password reset code"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        f"Your password reset code is {code}.\n\n"
        f"It expires in {RESET_CODE_TTL // 60} minutes. If you did not request this, ignore this e-mail."
    )
    port = int(os.environ.get("KLEVS_SMTP_PORT", "587"))
    user = os.environ.get("KLEVS_SMTP_USER", "")
    password = os.environ.get("KLEVS_SMTP_PASSWORD", "")
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("reset code e-mail failed for %s", email)
        return False
    return True
