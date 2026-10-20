"""Checks for the optional Cloudflare Pages static frontend build and API proxy."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_pages_build_command_and_api_proxy_are_configured():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == "node scripts/build-static.mjs"
    assert (ROOT / "functions" / "api" / "[[path]].js").is_file()
    proxy = (ROOT / "functions" / "api" / "[[path]].js").read_text(encoding="utf-8")
    assert "KLEVS_API_ORIGIN" in proxy
    assert "new Request(upstreamUrl, request)" in proxy
    landing = (ROOT / "static" / "landing.html").read_text(encoding="utf-8")
    assert '<a href="/admin">Operator sign in</a>' in landing
    auth = (ROOT / "static" / "auth.js").read_text(encoding="utf-8")
    assert re.search(r'if\s*\(s\.role === "admin"\)\s*\{\s*window\.location\.href = "/admin";', auth)


def test_pages_static_build_preserves_app_routes_when_node_is_available():
    if not shutil.which("npm"):
        pytest.skip("Node.js/npm is not installed")

    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "API routes proxy to the Cloudflare Worker" in build.stdout

    assert (ROOT / "dist" / "index.html").read_bytes() == (
        ROOT / "static" / "landing.html"
    ).read_bytes()
    assert (ROOT / "dist" / "app" / "index.html").read_bytes() == (
        ROOT / "static" / "index.html"
    ).read_bytes()
    assert (ROOT / "dist" / "admin" / "index.html").read_bytes() == (
        ROOT / "static" / "admin.html"
    ).read_bytes()
    for asset in ("app.js", "auth.js", "admin.js", "style.css", "landing.css"):
        assert (ROOT / "dist" / "static" / asset).is_file()


def test_pages_api_proxy_forwards_requests_when_node_is_available():
    if not shutil.which("node"):
        pytest.skip("Node.js is not installed")

    script = r"""
import assert from 'node:assert/strict';
import { onRequest } from './functions/api/[[path]].js';
let forwarded;
globalThis.fetch = async (request) => {
  forwarded = request;
  return new Response('{"status":"ok"}', { status: 200 });
};
const request = new Request('https://pages.example/api/payments?status=pending', {
  method: 'POST',
  headers: { 'content-type': 'application/json', authorization: 'Bearer test' },
  body: JSON.stringify({ plan: 'starter' }),
});
const response = await onRequest({ request, env: { KLEVS_API_ORIGIN: 'https://api.example.com' } });
assert.equal(response.status, 200);
assert.equal(forwarded.url, 'https://api.example.com/api/payments?status=pending');
assert.equal(forwarded.method, 'POST');
assert.equal(forwarded.headers.get('authorization'), 'Bearer test');
assert.deepEqual(await forwarded.json(), { plan: 'starter' });
const healthRequest = new Request('https://pages.example/api/health');
const fallback = await onRequest({ request: healthRequest, env: {} });
assert.equal(fallback.status, 200);
assert.equal(forwarded.url, 'https://klv-fx-trading-signals.klevco.workers.dev/api/health');
const insecure = await onRequest({
  request: healthRequest,
  env: { KLEVS_API_ORIGIN: 'http://api.example.com' },
});
assert.equal(insecure.status, 500);
"""
    subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
