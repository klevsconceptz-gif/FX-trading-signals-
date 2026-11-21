"""The one-time workflow that removes the retired admin secrets from the Worker.

These tests pin its safety rules as plain text checks, since PyYAML is not a repo dependency.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "delete-old-admin-secrets.yml").read_text(encoding="utf-8")
RETIRED = ["KFX_ADMIN_USERNAME", "KFX_ADMIN_PASSWORD", "KFX_ADMIN_PASSWORD_HASH"]


def test_runs_only_on_demand_from_main_with_the_confirmation_text():
    assert "workflow_dispatch:" in WORKFLOW
    assert "\n  push:" not in WORKFLOW and "\n  schedule:" not in WORKFLOW
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "inputs.confirm == 'DELETE OLD ADMIN SECRETS'" in WORKFLOW


def test_uses_the_production_environment_and_the_worker_config():
    assert "environment: production" in WORKFLOW
    assert "--config worker/wrangler.jsonc" in WORKFLOW


def test_deletes_only_the_three_retired_names():
    for name in RETIRED:
        assert name in WORKFLOW
    # The only names this job may delete are the retired ones; no current admin secret is touched.
    assert "wrangler secret delete" in WORKFLOW
    for current in ("ADMIN_USERNAME", "ADMIN_PASSWORD", "KFX_ADMIN_EMAIL"):
        assert f"wrangler secret delete {current}" not in WORKFLOW
        assert f'"{current}"' not in WORKFLOW
    assert "wrangler secret put" not in WORKFLOW


def test_refuses_to_run_while_code_still_reads_a_retired_name():
    assert 'grep -rn "KFX_ADMIN_USERNAME\\|KFX_ADMIN_PASSWORD" worker/src' in WORKFLOW


def test_secrets_only_enter_through_env_mappings_never_run_lines():
    for line in WORKFLOW.splitlines():
        if "${{ secrets." in line:
            assert ":" in line and "${{ secrets." in line.split(":", 1)[1], line
            assert line.strip().split(":", 1)[0].isupper() or "CLOUDFLARE" in line, line
