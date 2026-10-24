"""Guard tests for the Android and iOS shells (Capacitor) and their CI workflow."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_capacitor_config_names_the_app_and_the_bundle():
    config = json.loads((ROOT / "capacitor.config.json").read_text(encoding="utf-8"))
    assert config["appId"] == "com.klevs.fxtrading"
    assert config["webDir"] == "mobile/www"
    assert config["server"]["androidScheme"] == "https"
    assert config["server"]["iosScheme"] == "capacitor"


def test_native_projects_exist_and_use_the_same_app_id():
    gradle = (ROOT / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    assert 'applicationId "com.klevs.fxtrading"' in gradle
    assert "klevsVersionCode" in gradle
    assert "KLEVS_KEYSTORE_PATH" in gradle
    assert (ROOT / "android" / "gradlew").is_file()
    pbxproj = (ROOT / "ios" / "App" / "App.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert "PRODUCT_BUNDLE_IDENTIFIER = com.klevs.fxtrading;" in pbxproj


def _tracked_files() -> list[Path]:
    """Files git would commit. Skips when the checkout has no git metadata."""
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / name for name in listing.stdout.splitlines()]


def test_no_signing_material_or_api_secret_is_committed():
    tracked = _tracked_files()
    for path in tracked:
        assert not path.name.endswith((".keystore", ".jks", ".p12", ".mobileprovision")), path
    patterns = [re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"BEGIN CERTIFICATE")]
    for path in tracked:
        if not path.is_file() or path.suffix in {".jar", ".png", ".jpg", ".ttf", ".otf"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            assert not pattern.search(text), f"secret-looking content in {path.relative_to(ROOT)}"


def test_mobile_workflow_builds_both_platforms_as_artifacts_only():
    workflow = _workflow("mobile.yml")
    assert set(workflow["jobs"]) == {"android", "ios", "release"}
    assert workflow["permissions"] == {"contents": "read"}, "the default token is read-only"
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write"}
    text = (WORKFLOWS / "mobile.yml").read_text(encoding="utf-8")
    assert "./gradlew assembleRelease" in text
    assert "CODE_SIGNING_ALLOWED=NO" in text
    assert "xcodebuild -exportArchive" in text
    # no store upload: nothing in the workflow talks to Google Play or App Store Connect
    for store_word in ("supply", "upload-to-play", "altool", "notarytool", "pilot", "upload-app"):
        assert store_word not in text.lower()
    # the tag trigger is the only way the release job runs
    assert workflow["jobs"]["release"]["if"] == "startsWith(github.ref, 'refs/tags/mobile-v')"


def test_workflows_use_node_22_for_the_capacitor_cli():
    for name in ("mobile.yml", "cloudflare-deploy.yml", "signals.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "node-version: 22" in text, name
        assert "node-version: 20" not in text, name


def test_web_pages_load_the_runtime_shim_before_auth():
    for page in ("index.html", "admin.html", "landing.html"):
        html = (ROOT / "static" / page).read_text(encoding="utf-8")
        assert html.index("/static/runtime.js") < html.index("/static/auth.js"), page
    auth = (ROOT / "static" / "auth.js").read_text(encoding="utf-8")
    assert 'appRoute("admin")' in auth
    assert 'window.location.href = "/admin"' not in auth


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is not installed")
def test_mobile_bundle_points_at_the_worker_and_rejects_bad_origins():
    build = subprocess.run(["node", "scripts/build-mobile.mjs"], cwd=ROOT, capture_output=True, text=True,
                           env={"PATH": __import__("os").environ["PATH"], "KLEVS_API_ORIGIN": "https://api.example.test/"})
    assert build.returncode == 0, build.stderr
    runtime = (ROOT / "mobile" / "www" / "static" / "runtime.js").read_text(encoding="utf-8")
    assert "__KLEVS_API_ORIGIN__" not in runtime
    assert 'var BUILT_API_ORIGIN = "https://api.example.test";' in runtime
    assert (ROOT / "mobile" / "www" / "index.html").is_file()
    assert (ROOT / "mobile" / "www" / "admin.html").is_file()

    bad = subprocess.run(["node", "scripts/build-mobile.mjs"], cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": __import__("os").environ["PATH"], "KLEVS_API_ORIGIN": "http://insecure.example.test"})
    assert bad.returncode != 0
    assert "https origin" in bad.stderr
