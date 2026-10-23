"""Static checks for the Cloudflare deployment files (Dockerfile, .dockerignore, edge/).

These run offline and do not build the image or deploy anything. They catch drift:
an environment variable the code reads but the docs do not mention, a Worker that
sets a variable the app never reads, a Dockerfile COPY source that was renamed, a
build context that would ship secrets, or a non-Cloudflare dependency creeping back
into the deploy path.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "CLOUDFLARE_DEPLOYMENT.md"
REQUIREMENTS = ROOT / "docs" / "CLOUDFLARE_REQUIREMENTS.md"
ENV_PATTERN = r"\b((?:KLEVS|KFX)_[A-Z0-9_]+)\b"


def _code_env_vars() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "fxbot").glob("*.py"):
        names.update(re.findall(ENV_PATTERN, path.read_text(encoding="utf-8")))
    return names


def _container_settings() -> str:
    """The object literal the Worker passes to the container (the only variables the container receives)."""
    text = (ROOT / "edge" / "src" / "container-env.ts").read_text(encoding="utf-8")
    start = text.index("const candidates")
    return text[start:text.index("};", start)]


def _wrangler_config() -> dict:
    # wrangler.jsonc uses whole-line // comments only; strip those and parse as JSON.
    text = (ROOT / "edge" / "wrangler.jsonc").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return json.loads(text)


def test_deployment_guide_documents_every_variable_the_code_reads():
    doc = DOC.read_text(encoding="utf-8")
    missing = {name for name in _code_env_vars() if name not in doc}
    assert not missing, f"document these in {DOC.name}: {sorted(missing)}"


def test_worker_sets_only_variables_the_app_reads():
    keys = set(re.findall(r"^\s*((?:KLEVS|KFX)_[A-Z0-9_]+):", _container_settings(), flags=re.MULTILINE))
    assert keys, "container-env.ts should name the variables it passes on"
    unknown = keys - _code_env_vars()
    assert not unknown, f"the container receives variables the app never reads: {sorted(unknown)}"


def test_worker_never_sets_dev_reset_codes_or_hard_coded_secrets():
    assert "KLEVS_DEV_RESET_CODES" not in _container_settings()
    wrangler = (ROOT / "edge" / "wrangler.jsonc").read_text(encoding="utf-8")
    for secret in ("KFX_ADMIN_PASSWORD", "INTERNAL_TOKEN", "KLEVS_INTERNAL_TOKEN"):
        assert secret not in wrangler, f"{secret} must come from `wrangler secret put`, not wrangler.jsonc"


def test_wrangler_binds_r2_email_and_the_container_as_cloudflare_resources():
    config = _wrangler_config()
    assert config["workers_dev"] is False
    assert {b["binding"]: b["bucket_name"] for b in config["r2_buckets"]} == {"BACKUPS": "klevs-backups"}
    assert [b["name"] for b in config["send_email"]] == ["EMAIL"]
    container = config["containers"][0]
    assert container["class_name"] == "KlevsContainer"
    assert container["image"] == "../Dockerfile"
    assert container["max_instances"] == 1, "SQLite has one writer: exactly one container instance"
    assert config["durable_objects"]["bindings"] == [{"name": "KLEVS_CONTAINER", "class_name": "KlevsContainer"}]
    assert any("KlevsContainer" in m.get("new_sqlite_classes", []) for m in config["migrations"])
    assert set(config["vars"]) >= {"KLEVS_PUBLIC_ORIGIN", "KLEVS_MAIL_FROM"}


def test_dockerfile_copy_sources_exist():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("COPY "):
            continue
        parts = line.split()[1:]
        sources = [p for p in parts[:-1] if not p.startswith("--")]
        for src in sources:
            assert (ROOT / src).exists(), f"Dockerfile COPY source missing: {src}"


def test_dockerfile_runs_single_worker_as_non_root_on_port_8000():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r'"--workers",\s*"1"', text), "uvicorn must run one worker"
    assert re.search(r"^USER\s+klevs\s*$", text, flags=re.MULTILINE), "must not run as root"
    assert re.search(r"^EXPOSE 8000$", text, flags=re.MULTILINE)
    assert '"--port", "8000"' in text


def test_dockerfile_bakes_in_no_secrets_and_no_tunnel():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^(ENV|ARG)\s+.*(PASSWORD|TOKEN|SECRET)", text, flags=re.MULTILINE | re.IGNORECASE)
    for banned in ("cloudflared", "tunnel", "systemd", "psycopg"):
        assert banned not in text.lower(), f"Dockerfile should not mention {banned}"


def test_dockerignore_keeps_secrets_and_local_state_out_of_the_image():
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for pattern in (".env", ".env.*", ".dev.vars", ".dev.vars.*", ".git", ".venv", "data", "models", "*.db", "*.pkl", "edge"):
        assert pattern in lines, f".dockerignore should exclude {pattern}"


def test_no_systemd_or_vm_deploy_files_remain():
    assert not (ROOT / "deploy").exists(), "the VM/systemd deploy path was removed in favour of Cloudflare Containers"


def test_app_code_has_no_smtp_path():
    for path in list((ROOT / "fxbot").glob("*.py")) + list((ROOT / "edge" / "src").glob("*.ts")):
        text = path.read_text(encoding="utf-8").lower()
        assert "smtplib" not in text and "smtp" not in text, f"{path.name} still has an SMTP path"


def test_every_external_host_in_code_is_in_the_requirements_doc():
    """A new outbound host in fxbot/ must be added to the egress list in the requirements."""
    doc = REQUIREMENTS.read_text(encoding="utf-8")
    hosts: set[str] = set()
    for path in (ROOT / "fxbot").glob("*.py"):
        hosts.update(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8")))
    missing = {h for h in hosts if h not in doc}
    assert not missing, f"add these hosts to {REQUIREMENTS.name}: {sorted(missing)}"


def test_requirements_doc_names_every_cloudflare_service_the_path_uses():
    doc = REQUIREMENTS.read_text(encoding="utf-8")
    for service in ("Containers", "R2", "Email Service", "Workers"):
        assert service in doc, f"{REQUIREMENTS.name} should name {service}"
