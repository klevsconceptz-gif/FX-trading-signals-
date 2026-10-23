"""Database layer (SQLAlchemy 2.x).

* Development / single-server: SQLite file at ``data/klevs.db`` (created with
  owner-only permissions, git-ignored).
* Production: set ``KLEVS_DATABASE_URL`` to a PostgreSQL URL, e.g.
  ``postgresql+psycopg://klevs:***@db-host:5432/klevs?sslmode=require``.
  The schema is portable; only the URL changes.

Tables
------
users          accounts (password stored as PBKDF2 hash, never plain text)
user_sessions  bearer tokens — only the SHA-256 of each token is stored
payments       invoices: crypto or wire, awaiting → submitted → confirmed/rejected
access_grants  paid access windows (30 days per confirmed payment) and manual grants
settings       operator-managed key/value config (crypto wallets, wire details)
admin_audit_events  immutable operator activity records (no wallet/bank values)
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from . import replica

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = ROOT / "data" / "klevs.db"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    username_key: Mapped[str] = mapped_column(String(64), unique=True)   # lowercase, for lookups
    email: Mapped[str] = mapped_column(String(200), unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(16), default="user")       # user | admin
    created_at: Mapped[float] = mapped_column(Float)
    reset_hash: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    reset_expires: Mapped[float] = mapped_column(Float, default=0.0)
    reset_attempts: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0.0)


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invoice: Mapped[str] = mapped_column(String(16), unique=True)
    plan: Mapped[str] = mapped_column(String(16))
    amount_usd: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(8))                        # crypto | wire
    network: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    pay_to: Mapped[str] = mapped_column(Text)                            # snapshot of wallet / bank details
    # awaiting_payment -> submitted -> confirmed | rejected ; or cancelled
    status: Mapped[str] = mapped_column(String(24), index=True)
    reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)   # tx hash / wire ref
    sender_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    submitted_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    decided_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class AccessGrant(Base):
    __tablename__ = "access_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")    # active | revoked
    starts_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    granted_by: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id"), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AdminAuditEvent(Base):
    """Immutable record of an operator action; deliberately stores no payment secrets."""

    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[float] = mapped_column(Float, index=True)
    actor_username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48), index=True)
    target_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    target_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    grant_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reference: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(String(200))
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


# ---------------------------------------------------------------------------
# engine / sessions
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None
# File behind the SQLite engine, when replication applies (None for PostgreSQL and :memory:).
_sqlite_path: Optional[str] = None


def database_url() -> str:
    url = os.environ.get("KLEVS_DATABASE_URL", "").strip()
    if url:
        return url
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


def init_engine(url: Optional[str] = None) -> Engine:
    """(Re)configure the global engine and create any missing tables."""
    global _engine, _factory, _sqlite_path
    with _lock:
        url = url or database_url()
        sqlite = url.startswith("sqlite")
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False} if sqlite else {},
            pool_pre_ping=not sqlite,
        )
        if sqlite:
            @event.listens_for(engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        _sqlite_path = None
        if sqlite and ":memory:" not in url:
            path = url.split("sqlite:///", 1)[-1]
            if path:
                _sqlite_path = path
                # Restore from the R2 replica before the first connection. Raises on a bad snapshot.
                replica.restore(path)
        Base.metadata.create_all(engine)
        if _sqlite_path and Path(_sqlite_path).exists():
            os.chmod(_sqlite_path, 0o600)        # owner-only: it holds password hashes
        _engine = engine
        _factory = sessionmaker(bind=engine, expire_on_commit=False)
        return engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    return _engine


@contextmanager
def db_session() -> Iterator[Session]:
    """Transaction scope: commits on success, rolls back on any exception.

    When the database is SQLite and a replica is configured, a commit that
    changed any row is copied to the replica before this returns. Bulk
    UPDATE and DELETE statements count too, because the check reads the
    connection's change counter rather than the ORM's dirty list.
    """
    if _factory is None:
        init_engine()
    session = _factory()
    replicated = _sqlite_path is not None and replica.enabled()
    changes_before = 0
    dbapi_conn = None
    if replicated:
        dbapi_conn = session.connection().connection.dbapi_connection
        changes_before = dbapi_conn.total_changes
    wrote = False
    try:
        yield session
        if replicated:
            wrote = dbapi_conn.total_changes != changes_before or bool(
                session.new or session.dirty or session.deleted
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if wrote:
        replica.replicate(_sqlite_path)


def reset_database() -> None:
    """Drop and recreate every table (tests and explicit operator resets only)."""
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
