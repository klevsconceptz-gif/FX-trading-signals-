"""Checks that the web app copes with weak mobile connections: every API call has a time limit,
reads are retried, writes are never retried automatically, and failures explain themselves."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_runtime_defines_one_timed_request_helper_for_every_page():
    runtime = _read("runtime.js")
    assert "function fetchWithTimeout(" in runtime
    assert "new AbortController()" in runtime
    assert "var DEFAULT_TIMEOUT_MS = 20000;" in runtime
    # Reads are retried once; writes are not retried, because the server may already have acted.
    assert 'method === "GET" ? 1 : 0' in runtime
    assert "failure.network = true;" in runtime
    assert "request: request," in runtime
    # runtime.js loads first on every page that talks to the API.
    for page in ("landing.html", "index.html", "admin.html"):
        html = _read(page)
        assert html.index('src="/static/runtime.js"') < html.index('src="/static/auth.js"'), page


def test_every_api_caller_goes_through_the_helper_not_bare_fetch():
    auth = _read("auth.js")
    app = _read("app.js")
    admin = _read("admin.js")
    assert "window.KLEVS.request" in auth and "await request(url, opts)" in auth
    assert "window.KLEVS.request(url, { ...opts, headers }, { timeoutMs })" in app
    assert "window.KLEVS.request" in admin
    for source in (auth, app, admin):
        assert not re.search(r"(?<![\w.])await fetch\(", source), "bare fetch left behind"


def test_dashboard_scan_gets_a_longer_limit_and_reports_connection_problems_plainly():
    app = _read("app.js")
    assert 'url.startsWith("/api/scan") ? 90000 : 20000' in app
    assert "e.network" in app and "Connection problem" in app
    # Reconnecting after a failed scan loads the signals again without a click.
    assert 'window.addEventListener("online"' in app


def test_offline_session_is_kept_and_marked_instead_of_signing_the_user_out():
    auth = _read("auth.js")
    app = _read("app.js")
    assert "if (err.status === 401) { writeSession(null); return null; }" in auth
    assert "if (err.network) return { ...s, offline: true };" in auth
    assert "if (s && s.offline) return lockPaywall(OFFLINE_PAYWALL_MESSAGE);" in app
    assert "includes the last known access when offline" in app


def test_registration_warns_that_a_dropped_connection_may_still_have_created_the_account():
    auth = _read("auth.js")
    assert "The connection dropped while creating your account." in auth



def test_scripts_avoid_syntax_older_browsers_cannot_run():
    # Optional chaining (?.) and nullish coalescing (??) are ES2020; the pages must run on ES2018.
    for name in ("runtime.js", "auth.js", "app.js", "admin.js", "landing.js"):
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", _read(name), flags=re.S)
        assert not re.search(r"\?\.(?![0-9])", code), f"{name}: optional chaining"
        assert "??" not in code, f"{name}: nullish coalescing"
