"""Account system: registration, credential login, forgot-password.

Security model (stdlib-only, no extra dependencies):

* Passwords are **never stored in plain text** — each password is hashed
  with PBKDF2-HMAC-SHA256 (200k iterations) and a unique random salt.
* Reset codes are stored **hashed** with a short expiry (email auth):
  the code is delivered to the account email and verified against its
  hash — the raw code is never persisted.
* Login accepts any of the three identifiers — username, email or phone —
  plus the password (constant-time hash comparison).

Password policy: minimum 10 characters containing at least one
uppercase letter, one lowercase letter, one number and one symbol.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

PBKDF2_ITERATIONS = 200_000
RESET_CODE_TTL = 15 * 60          # seconds
RESET_MAX_ATTEMPTS = 5            # wrong-code attempts before invalidation
MIN_PASSWORD_LEN = 10

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-().]{6,19}$")


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
    return {"valid": all(checks.values()), "checks": checks,
            "min_length": MIN_PASSWORD_LEN}


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


# ---------------------------------------------------------------------------
# account store
# ---------------------------------------------------------------------------

@dataclass
class Account:
    username: str
    email: str
    phone: str
    password_hash: str
    created_at: float = field(default_factory=time.time)
    plan: str = "demo"
    role: str = "user"                 # "user" | "admin" (admin = unrestricted)
    reset_hash: Optional[str] = None
    reset_expires: float = 0.0
    reset_attempts: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "email": self.email,
            "phone": self.phone,
            "plan": self.plan,
            "role": self.role,
            "created_at": self.created_at,
        }


class AccountStore:
    """In-memory account registry (swap for a DB table in production)."""

    def __init__(self) -> None:
        self._by_username: dict[str, Account] = {}
        self._by_email: dict[str, Account] = {}
        self._by_phone: dict[str, Account] = {}
        self._lock = threading.Lock()

    # -- registration -------------------------------------------------------

    def register(self, username: str, email: str, phone: str,
                 password: str, plan: str = "demo") -> Account:
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

        with self._lock:
            if username.lower() in {u.lower() for u in self._by_username}:
                raise ValueError("username is already taken")
            if email in self._by_email:
                raise ValueError("an account with that email already exists")
            if phone in self._by_phone:
                raise ValueError("an account with that phone number already exists")
            acc = Account(username=username, email=email, phone=phone,
                          password_hash=hash_password(password), plan=plan)
            self._by_username[username] = acc
            self._by_email[email] = acc
            self._by_phone[phone] = acc
        return acc

    # -- login (any identifier + password) ----------------------------------

    def authenticate(self, identifier: str, password: str) -> Account:
        ident = identifier.strip()
        with self._lock:
            acc = (self._by_username.get(ident)
                   or self._by_email.get(ident.lower())
                   or self._by_phone.get(ident))
            # fall back to normalized-phone match (spaces/dashes differ)
            if acc is None and re.fullmatch(r"[0-9+\s\-().]+", ident):
                norm = re.sub(r"[^\d+]", "", ident)
                for phone, candidate in self._by_phone.items():
                    if re.sub(r"[^\d+]", "", phone) == norm:
                        acc = candidate
                        break
            if acc is None:
                raise ValueError("no account found for that username, email or phone")
            if not verify_password(password, acc.password_hash):
                raise PermissionError("incorrect password")
            return acc

    # -- forgot / reset password (email auth) --------------------------------

    def issue_reset(self, email: str) -> str:
        """Create a one-time reset code, store ONLY its hash. Returns the raw
        code so the caller can 'email' it (demo builds display it)."""
        email = email.strip().lower()
        with self._lock:
            acc = self._by_email.get(email)
            if acc is None:
                raise KeyError("no account found for that email")
            code = f"{secrets.randbelow(10_000_000):07d}"
            salt = secrets.token_bytes(8)
            acc.reset_hash = (salt.hex() + ":"
                              + hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 50_000).hex())
            acc.reset_expires = time.time() + RESET_CODE_TTL
            acc.reset_attempts = 0
        return code

    def reset_password(self, email: str, code: str, new_password: str) -> Account:
        email = email.strip().lower()
        err = policy_error(new_password)
        if err:
            raise ValueError(err)
        with self._lock:
            acc = self._by_email.get(email)
            if acc is None or acc.reset_hash is None:
                raise KeyError("no reset was requested for that email")
            if time.time() > acc.reset_expires:
                acc.reset_hash = None
                raise ValueError("reset code has expired — request a new one")
            if acc.reset_attempts >= RESET_MAX_ATTEMPTS:
                acc.reset_hash = None
                raise ValueError("too many wrong attempts — request a new code")
            salt_hex, digest_hex = acc.reset_hash.split(":", 1)
            candidate = hashlib.pbkdf2_hmac("sha256", code.encode(),
                                            bytes.fromhex(salt_hex), 50_000)
            if not hmac.compare_digest(candidate, bytes.fromhex(digest_hex)):
                acc.reset_attempts += 1
                raise ValueError("incorrect reset code")
            acc.password_hash = hash_password(new_password)
            acc.reset_hash = None
            acc.reset_attempts = 0
            return acc

    # -- admin seeding (operator-provisioned, never self-service) -----------

    def seed_admin(self, username: str, password: str,
                   email: Optional[str] = None, phone: Optional[str] = None) -> Account:
        """Create or rotate the admin account.

        Admin usernames are not restricted to the user-facing username rule
        (so an address such as ``Admin@klev.fx`` works), but the password policy
        still applies.  Email defaults to the username when that is an address.
        Calling again with the same username rotates the password in place.
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

        with self._lock:
            for index, key in ((self._by_username, username), (self._by_email, email)):
                hit = index.get(key)
                if hit is not None and hit.role != "admin":
                    raise ValueError("admin credentials clash with an existing user account")
            acc = self._by_username.get(username) or self._by_email.get(email)
            if acc is None:
                acc = Account(username=username, email=email, phone=phone,
                              password_hash=hash_password(password),
                              plan="admin", role="admin")
                self._by_username[username] = acc
                self._by_email[email] = acc
            else:
                acc.password_hash = hash_password(password)
                acc.plan, acc.role = "admin", "admin"
                acc.reset_hash, acc.reset_attempts = None, 0
                if phone and phone != acc.phone:
                    self._by_phone.pop(acc.phone, None)
                    acc.phone = phone
            if acc.phone:
                self._by_phone[acc.phone] = acc
        return acc

    def set_plan(self, email: str, plan: str) -> None:
        with self._lock:
            acc = self._by_email.get(email.strip().lower())
            if acc is not None:
                acc.plan = plan


ACCOUNTS = AccountStore()
