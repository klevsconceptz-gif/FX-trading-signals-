"""Regression checks for SEO metadata, crawler files and cross-device sign-in markup."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CANONICAL = "https://fx-trading-signals.pages.dev"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_landing_has_core_seo_metadata():
    html = _read(STATIC / "landing.html")
    title = re.search(r"<title>(.*?)</title>", html).group(1)
    assert 20 <= len(title) <= 70
    desc = re.search(r'<meta name="description" content="([^"]+)"', html).group(1)
    assert 50 <= len(desc) <= 170
    assert f'<link rel="canonical" href="{CANONICAL}/" />' in html
    assert 'name="robots" content="index, follow' in html
    assert 'property="og:title"' in html and 'property="og:url"' in html
    assert 'name="twitter:card"' in html
    assert 'rel="icon"' in html and 'rel="manifest"' in html
    # structured data must be valid JSON and describe the app
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    graph = json.loads(ld.group(1))["@graph"]
    assert {item["@type"] for item in graph} == {"WebSite", "SoftwareApplication"}
    assert re.search(r"<h1>", html) and html.count("<h1>") == 1


def test_members_and_operator_pages_stay_out_of_search():
    assert 'content="noindex, nofollow"' in _read(STATIC / "index.html")
    assert 'content="noindex, nofollow"' in _read(STATIC / "admin.html")


def test_crawler_files_block_the_api_and_point_to_sitemap():
    robots = _read(STATIC / "robots.txt")
    assert "Disallow: /api/" in robots
    # /app and /admin carry noindex; blocking them here would hide that instruction from crawlers.
    assert "Disallow: /app" not in robots and "Disallow: /admin" not in robots
    assert f"Sitemap: {CANONICAL}/sitemap.xml" in robots


def test_pages_headers_are_shipped_to_the_site_root():
    headers = _read(STATIC / "_headers")
    assert "X-Content-Type-Options: nosniff" in headers
    assert "X-Frame-Options: DENY" in headers
    assert "Cache-Control: public, max-age=3600" in headers
    assert '"_headers"' in _read(ROOT / "scripts" / "build-static.mjs")
    sitemap = _read(STATIC / "sitemap.xml")
    assert f"<loc>{CANONICAL}/</loc>" in sitemap
    assert "/app" not in sitemap and "/admin" not in sitemap


def test_build_copies_crawler_files_to_site_root():
    build = _read(ROOT / "scripts" / "build-static.mjs")
    assert '"robots.txt"' in build and '"sitemap.xml"' in build


def test_manifest_is_valid_json_with_an_icon():
    manifest = json.loads(_read(STATIC / "site.webmanifest"))
    assert manifest["display"] == "standalone"
    assert manifest["icons"] and manifest["icons"][0]["src"].endswith("favicon.svg")


def test_responsive_stylesheet_loads_last_on_every_page():
    for page in ("landing.html", "index.html", "admin.html"):
        links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', _read(STATIC / page))
        assert links[-1] == "/static/responsive.css", page


def test_login_uses_current_password_and_no_capitalisation_on_identifiers():
    auth = _read(STATIC / "auth.js")
    assert 'checklist: false, autocomplete: "current-password"' in auth
    assert 'id="log-ident" required maxlength="200"' in auth
    login_block = auth[auth.index('id="log-ident"'): auth.index('id="log-submit"')]
    assert 'autocapitalize="none"' in login_block


def test_touch_targets_and_16px_inputs_for_touch_screens():
    css = _read(STATIC / "responsive.css")
    assert "@media (pointer: coarse)" in css
    assert "font-size: 16px !important" in css
    assert "min-height: 44px" in css


def test_browser_scripts_parse_on_older_browsers():
    # Optional chaining (?.) and nullish coalescing (??) need iOS 13.4+ and Chrome 80+. Older phones
    # would fail to load the whole script and sign-up would stop working. Keep to ES2018.
    for name in ("admin.js", "app.js", "auth.js", "landing.js", "runtime.js"):
        src = _read(STATIC / name)
        assert not re.search(r"\?\?", src), f"{name} uses ??"
        assert not re.search(r"[\w)\]]\?\.[A-Za-z_\[(]", src), f"{name} uses ?."
