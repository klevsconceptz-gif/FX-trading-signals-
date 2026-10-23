"""SQLite replication to Cloudflare R2, through the Worker's internal snapshot route.

The container's disk is ephemeral, so the SQLite file is copied to an R2 object
after every commit that changes data, and restored from that object when the
container starts with no local database.

    * ``restore`` runs once, before the database opens. It is skipped when a
      non-empty local file exists. A missing snapshot is normal on first start.
      Any other failure raises and stops startup, so the app never runs on an
      empty database by accident.
    * ``replicate`` runs synchronously after each write-bearing commit. A failed
      upload marks the replica ``pending`` and a background thread retries every
      ``RETRY_SECONDS`` until it succeeds.
    * ``status`` reports ``disabled`` (no replica configured), ``ok``, or
      ``pending``. It is shown on ``/api/health``.

Environment (set by the Worker, see ``edge/wrangler.jsonc``):

    KLEVS_REPLICA_URL       full URL of the snapshot route, e.g. https://<origin>/__internal/snapshot
    KLEVS_INTERNAL_TOKEN    shared secret; sent as the ``X-Internal-Token`` header
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("klevs.replica")

RETRY_SECONDS = 15
MAX_BYTES = 256 * 1024 * 1024          # matches the Worker's cap on PUT bodies
HTTP_TIMEOUT = 60


class ReplicaError(RuntimeError):
    """The snapshot could not be read, written, or verified."""


_lock = threading.Lock()
_pending = False
_retry_thread: threading.Thread | None = None


def _url() -> str:
    return os.environ.get("KLEVS_REPLICA_URL", "").strip()


def _token() -> str:
    return os.environ.get("KLEVS_INTERNAL_TOKEN", "").strip()


def enabled() -> bool:
    return bool(_url() and _token())


def status() -> str:
    if not enabled():
        return "disabled"
    return "pending" if _pending else "ok"


def _request(method: str, data: bytes | None = None) -> urllib.request.Request:
    headers = {"x-internal-token": _token(), "user-agent": "klevs-fx/1.0"}
    if data is not None:
        headers["content-type"] = "application/octet-stream"
    return urllib.request.Request(_url(), data=data, method=method, headers=headers)


def fetch_snapshot() -> bytes | None:
    """Return the stored snapshot, or None when none has been written yet."""
    try:
        with urllib.request.urlopen(_request("GET"), timeout=HTTP_TIMEOUT) as response:
            if response.status != 200:
                raise ReplicaError(f"snapshot GET answered HTTP {response.status}")
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ReplicaError(f"snapshot GET answered HTTP {exc.code}") from None
    except OSError as exc:
        raise ReplicaError("snapshot route could not be reached") from exc
    if len(data) > MAX_BYTES:
        raise ReplicaError("snapshot is larger than the allowed size")
    return data


def push_snapshot(data: bytes) -> None:
    """Store a snapshot. The Worker answers 204 on success."""
    if len(data) > MAX_BYTES:
        raise ReplicaError("database is larger than the replica size limit")
    try:
        with urllib.request.urlopen(_request("PUT", data), timeout=HTTP_TIMEOUT) as response:
            status_code = response.status
    except urllib.error.HTTPError as exc:
        raise ReplicaError(f"snapshot PUT answered HTTP {exc.code}") from None
    except OSError as exc:
        raise ReplicaError("snapshot route could not be reached") from exc
    if status_code != 204:
        raise ReplicaError(f"snapshot PUT answered HTTP {status_code}, expected 204")


def snapshot_bytes(db_path: str | Path) -> bytes:
    """Take a consistent copy of a live WAL-mode database using the SQLite backup API."""
    db_path = Path(db_path)
    tmp = db_path.with_name(db_path.name + ".snapshot")
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    try:
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


def restore(db_path: str | Path) -> bool:
    """Restore the database from the replica when no local copy exists.

    Returns True when a snapshot was restored. Raises ReplicaError when a
    snapshot exists but cannot be used. The local file is never replaced
    otherwise.
    """
    if not enabled():
        return False
    path = Path(db_path)
    if path.exists() and path.stat().st_size > 0:
        return False
    data = fetch_snapshot()
    if data is None:
        log.info("no replica snapshot yet; starting with an empty database")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".restore")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    try:
        check = sqlite3.connect(tmp)
        try:
            row = check.execute("PRAGMA quick_check").fetchone()
        finally:
            check.close()
    except sqlite3.DatabaseError as exc:
        tmp.unlink(missing_ok=True)
        raise ReplicaError("snapshot is not a readable SQLite database") from exc
    if not row or row[0] != "ok":
        tmp.unlink(missing_ok=True)
        raise ReplicaError("snapshot failed the SQLite integrity check")
    # A WAL or SHM file left from an older database would be applied to the restored one.
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    log.info("database restored from replica snapshot")
    return True


def _set_pending(value: bool) -> None:
    global _pending
    with _lock:
        _pending = value


def _ensure_retry(db_path: str | Path) -> None:
    global _retry_thread
    with _lock:
        if _retry_thread is not None and _retry_thread.is_alive():
            return
        _retry_thread = threading.Thread(
            target=_retry_loop, args=(db_path,), name="klevs-replica-retry", daemon=True
        )
        _retry_thread.start()


def _retry_loop(db_path: str | Path) -> None:
    global _retry_thread
    while True:
        time.sleep(RETRY_SECONDS)
        if not _pending:
            break
        try:
            push_snapshot(snapshot_bytes(db_path))
        except (ReplicaError, sqlite3.Error, OSError) as exc:
            log.warning("replica retry failed, will try again: %s", exc)
            continue
        _set_pending(False)
        log.info("replica caught up after a failed upload")
        break
    with _lock:
        _retry_thread = None


def replicate(db_path: str | Path) -> None:
    """Upload the current database after a write. Never raises: a failure marks the replica pending."""
    if not enabled():
        return
    try:
        push_snapshot(snapshot_bytes(db_path))
    except (ReplicaError, sqlite3.Error, OSError) as exc:
        log.error("replication failed, marked pending and will retry: %s", exc)
        _set_pending(True)
        _ensure_retry(db_path)
        return
    _set_pending(False)
