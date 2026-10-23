"""Static checks for the deployment files on the existing Cloudflare route.

The deploy path is the Worker in worker/ (D1 and KV) plus the Pages function in
functions/. These checks run offline. They catch drift: a variable the Worker or
the app reads that the guide does not document, a binding that would bring in a
paid or non-Cloudflare service, or a Pages proxy that no longer points at the Worker.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DOC = ROOT / "docs" / "CLOUDFLARE_DEPLOYMENT.md"
REQUIREMENTS = ROOT / "docs" / "CLOUDFLARE_REQUIREMENTS.md"
ENV_PATTERN = r"\b((?:KLEVS|KFX)_[A-Z0-9_]+)\b"


def _app_env_vars() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "fxbot").glob("*.py"):
        names.update(re.findall(ENV_PATTERN, path.read_text(encoding="utf-8")))
    return names


def _worker_env_vars() -> set[str]:
    names: set[str] = set()
    for path in list((ROOT / "worker" / "src").glob("*.js")) + list((ROOT / "functions").rglob("*.js")):
        names.update(re.findall(r"\benv\.([A-Z][A-Z0-9_]+)\b", path.read_text(encoding="utf-8")))
    return names


def _worker_config() -> dict:
    return json.loads((ROOT / "worker" / "wrangler.jsonc").read_text(encoding="utf-8"))


def test_deployment_guide_documents_every_variable_the_app_reads():
    doc = DEPLOY_DOC.read_text(encoding="utf-8")
    missing = {name for name in _app_env_vars() if name not in doc}
    assert not missing, f"document these in {DEPLOY_DOC.name}: {sorted(missing)}"


def test_deployment_guide_documents_every_variable_the_worker_reads():
    doc = DEPLOY_DOC.read_text(encoding="utf-8")
    missing = {name for name in _worker_env_vars() if name not in doc}
    assert not missing, f"document these in {DEPLOY_DOC.name}: {sorted(missing)}"


def test_worker_binds_only_free_tier_cloudflare_resources():
    config = _worker_config()
    assert [b["binding"] for b in config["d1_databases"]] == ["klev"]
    assert [b["binding"] for b in config["kv_namespaces"]] == ["voom"]
    for paid_or_stored_elsewhere in ("r2_buckets", "containers", "send_email", "durable_objects", "routes"):
        assert paid_or_stored_elsewhere not in config, (
            f"worker/wrangler.jsonc must not bind {paid_or_stored_elsewhere}: it is not part of the existing deploy"
        )


def test_no_container_or_bucket_code_on_the_worker_route():
    for path in list((ROOT / "worker").rglob("*.js")) + list((ROOT / "functions").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for banned in ("BACKUPS", "send_email", "Container", "R2", "__internal"):
            assert banned not in text, f"{path.relative_to(ROOT)} must not use {banned}"


def test_pages_function_proxies_to_the_worker_and_declares_its_origin_variable():
    text = (ROOT / "functions" / "api" / "[[path]].js").read_text(encoding="utf-8")
    assert "klv-fx-trading-signals.klevco.workers.dev" in text
    assert "KLEVS_API_ORIGIN" in text


def test_dockerfile_copy_sources_exist():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("COPY "):
            continue
        parts = line.split()[1:]
        for src in [p for p in parts[:-1] if not p.startswith("--")]:
            assert (ROOT / src).exists(), f"Dockerfile COPY source missing: {src}"


def test_dockerignore_keeps_secrets_and_local_state_out_of_the_image():
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for pattern in (".env", ".env.*", ".git", ".venv", "data", "models", "*.db", "*.pkl"):
        assert pattern in lines, f".dockerignore should exclude {pattern}"


def test_every_external_host_in_code_is_in_the_requirements_doc():
    """A new outbound host in fxbot/ must be added to the egress list in the requirements."""
    doc = REQUIREMENTS.read_text(encoding="utf-8")
    hosts: set[str] = set()
    for path in (ROOT / "fxbot").glob("*.py"):
        hosts.update(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8")))
    missing = {h for h in hosts if h not in doc}
    assert not missing, f"add these hosts to {REQUIREMENTS.name}: {sorted(missing)}"


def test_requirements_doc_states_the_free_tier_limits_and_open_costs():
    doc = REQUIREMENTS.read_text(encoding="utf-8")
    for phrase in ("10 ms", "Workers Paid", "D1", "KV", "signal engine", "KLEVS_RESET_EMAIL_URL"):
        assert phrase in doc, f"{REQUIREMENTS.name} should state: {phrase}"
