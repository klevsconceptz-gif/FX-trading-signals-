"""SQLite replica: restore on start, replicate after writes, retry when R2 is unreachable."""

import os
import sqlite3
import time

import pytest
from sqlalchemy import update

from fxbot import db, replica
from fxbot.db import Setting, db_session
from tests.fake_worker import FakeWorker


@pytest.fixture(autouse=True)
def _restore_engine():
    """Tests re-point the global engine at temp files; put the suite's database back afterwards."""
    original = os.environ.get("KLEVS_DATABASE_URL")
    yield
    db.init_engine(original)


@pytest.fixture()
def worker():
    fake = FakeWorker()
    base = fake.start()
    yield fake, base
    fake.stop()


@pytest.fixture()
def replica_env(monkeypatch, worker):
    fake, base = worker
    monkeypatch.setenv("KLEVS_REPLICA_URL", base + "/__internal/snapshot")
    monkeypatch.setenv("KLEVS_INTERNAL_TOKEN", fake.token)
    monkeypatch.setattr(replica, "RETRY_SECONDS", 0.05)
    replica._set_pending(False)
    return fake


def _make_db(path, rows=("alpha", "beta")):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO settings VALUES (?, ?)", [(r, r.upper()) for r in rows])
    conn.commit()
    conn.close()


def _rows(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT key FROM settings"))
    finally:
        conn.close()


def test_disabled_without_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("KLEVS_REPLICA_URL", raising=False)
    monkeypatch.delenv("KLEVS_INTERNAL_TOKEN", raising=False)
    assert replica.enabled() is False
    assert replica.status() == "disabled"
    assert replica.restore(tmp_path / "x.db") is False
    replica.replicate(tmp_path / "x.db")          # no-op, must not raise


def test_snapshot_is_a_consistent_copy_of_a_wal_database(tmp_path):
    src = tmp_path / "live.db"
    _make_db(src)
    data = replica.snapshot_bytes(src)
    copy = tmp_path / "copy.db"
    copy.write_bytes(data)
    assert _rows(copy) == ["alpha", "beta"]
    assert not (tmp_path / "live.db.snapshot").exists()


def test_restore_brings_back_a_missing_database(replica_env, tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    replica_env.snapshot = replica.snapshot_bytes(src)

    target = tmp_path / "data" / "klevs.db"
    assert replica.restore(target) is True
    assert _rows(target) == ["alpha", "beta"]
    assert (target.stat().st_mode & 0o777) == 0o600


def test_restore_is_skipped_when_a_local_database_exists(replica_env, tmp_path):
    src = tmp_path / "src.db"
    _make_db(src, rows=("remote",))
    replica_env.snapshot = replica.snapshot_bytes(src)

    local = tmp_path / "local.db"
    _make_db(local, rows=("local",))
    assert replica.restore(local) is False
    assert _rows(local) == ["local"]
    assert not any(method == "GET" for method, _ in replica_env.requests)


def test_restore_with_no_snapshot_starts_empty(replica_env, tmp_path):
    target = tmp_path / "klevs.db"
    assert replica.restore(target) is False
    assert not target.exists()


def test_corrupt_snapshot_stops_the_restore_and_leaves_no_file(replica_env, tmp_path):
    replica_env.snapshot = b"this is not a database" * 100
    target = tmp_path / "klevs.db"
    with pytest.raises(replica.ReplicaError):
        replica.restore(target)
    assert not target.exists()
    assert not (tmp_path / "klevs.db.restore").exists()


def test_restore_removes_stale_wal_and_shm(replica_env, tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    replica_env.snapshot = replica.snapshot_bytes(src)
    target = tmp_path / "klevs.db"
    (tmp_path / "klevs.db-wal").write_bytes(b"stale wal from an older database")
    (tmp_path / "klevs.db-shm").write_bytes(b"stale shm")
    replica.restore(target)
    assert not (tmp_path / "klevs.db-wal").exists()
    assert not (tmp_path / "klevs.db-shm").exists()


def test_wrong_token_is_refused(replica_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KLEVS_INTERNAL_TOKEN", "not-the-token")
    with pytest.raises(replica.ReplicaError):
        replica.restore(tmp_path / "klevs.db")


def test_failed_upload_marks_pending_and_retry_catches_up(replica_env, tmp_path):
    src = tmp_path / "klevs.db"
    _make_db(src)
    replica_env.fail_puts = 1
    replica.replicate(src)
    assert replica.status() == "pending"

    deadline = time.time() + 5
    while replica.status() != "ok" and time.time() < deadline:
        time.sleep(0.02)
    assert replica.status() == "ok"
    assert replica_env.snapshot is not None
    assert replica_env.snapshot == replica.snapshot_bytes(src)


def test_write_commit_is_replicated_before_db_session_returns(replica_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KLEVS_DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    db.init_engine()
    with db_session() as s:
        s.add(Setting(key="fee_bps", value="5"))
    assert replica_env.snapshot is not None
    assert replica.status() == "ok"


def test_bulk_update_is_replicated_too(replica_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KLEVS_DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    db.init_engine()
    with db_session() as s:
        s.add(Setting(key="fee_bps", value="5"))
    before = replica_env.snapshot
    with db_session() as s:
        s.execute(update(Setting).where(Setting.key == "fee_bps").values(value="9"))
    assert replica_env.snapshot != before


def test_read_only_session_does_not_replicate(replica_env, monkeypatch, tmp_path):
    monkeypatch.setenv("KLEVS_DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    db.init_engine()
    with db_session() as s:
        s.add(Setting(key="fee_bps", value="5"))
    puts = sum(1 for method, _ in replica_env.requests if method == "PUT")
    with db_session() as s:
        s.query(Setting).all()
    assert sum(1 for method, _ in replica_env.requests if method == "PUT") == puts


def test_database_restored_from_replica_after_local_loss(replica_env, monkeypatch, tmp_path):
    path = tmp_path / "app.db"
    monkeypatch.setenv("KLEVS_DATABASE_URL", f"sqlite:///{path}")
    db.init_engine()
    with db_session() as s:
        s.add(Setting(key="signal_weight", value="0.7"))
    path.unlink()                                  # the container's disk is gone
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"app.db{suffix}").unlink(missing_ok=True)

    db.init_engine()                               # a fresh container starts here
    with db_session() as s:
        assert s.get(Setting, "signal_weight").value == "0.7"
