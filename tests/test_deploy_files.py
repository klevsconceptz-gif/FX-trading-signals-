"""Static checks for the deployment files (Dockerfile, .dockerignore, deploy/).

These run offline and do not build the image. They catch drift: a new environment
variable the code reads but the template does not document, a Dockerfile COPY
source that was renamed, or a build context that would ship secrets.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _code_env_vars() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "fxbot").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"\b((?:KLEVS|KFX)_[A-Z0-9_]+)\b", text))
    return names


def _template_env_vars() -> set[str]:
    text = (ROOT / "deploy" / "klevs.env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^((?:KLEVS|KFX)_[A-Z0-9_]+)=", text, flags=re.MULTILINE))


def test_env_template_documents_every_variable_the_code_reads():
    missing = _code_env_vars() - _template_env_vars()
    assert not missing, f"document these in deploy/klevs.env.example: {sorted(missing)}"


def test_env_template_holds_no_real_secrets():
    # Every credential-like key must be empty or an obvious placeholder.
    text = (ROOT / "deploy" / "klevs.env.example").read_text(encoding="utf-8")
    for key in ("KFX_ADMIN_PASSWORD", "KLEVS_SMTP_PASSWORD"):
        match = re.search(rf"^{key}=(.*)$", text, flags=re.MULTILINE)
        assert match is not None, key
        assert match.group(1).strip() == "", f"{key} must be empty in the template"
    assert "CHANGE_ME" in text  # database password placeholder is explicit


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


def test_dockerfile_runs_single_worker_as_non_root():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r'"--workers",\s*"1"', text), "uvicorn must run one worker"
    assert re.search(r"^USER\s+klevs\s*$", text, flags=re.MULTILINE), "must not run as root"


def test_dockerignore_keeps_secrets_and_local_state_out_of_the_image():
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for pattern in (".env", ".env.*", ".git", ".venv", "data", "models", "*.db", "*.pkl"):
        assert pattern in lines, f".dockerignore should exclude {pattern}"


def test_systemd_unit_binds_loopback_only():
    text = (ROOT / "deploy" / "klevs.service").read_text(encoding="utf-8")
    exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert "--host 127.0.0.1" in exec_line
    assert "0.0.0.0" not in exec_line
