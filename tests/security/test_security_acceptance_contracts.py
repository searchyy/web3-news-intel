from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_security_acceptance_script_passes_non_strict_contracts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/security_acceptance.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "security-acceptance: PASS" in result.stdout


def test_live_canary_workflow_is_not_pr_gate() -> None:
    data = _workflow("live-source-canary.yml")
    triggers = set(data["on"])
    assert "pull_request" not in triggers
    assert {"workflow_dispatch", "schedule"} <= triggers


def test_deepseek_live_workflow_is_manual_only_and_protected() -> None:
    data = _workflow("deepseek-test.yml")
    assert set(data["on"]) == {"workflow_dispatch"}
    job = data["jobs"]["deepseek-live-test"]
    assert job["environment"] == "deepseek-live"
    workflow_text = (ROOT / ".github" / "workflows" / "deepseek-test.yml").read_text(
        encoding="utf-8"
    )
    assert "secrets.DEEPSEEK_API_KEY" in workflow_text
    assert "pull_request" not in workflow_text


def test_validate_sources_rejects_private_enabled_source(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        """
sources:
  private_source:
    name: Private
    source_type: tier1_media
    adapter: rss
    url: http://127.0.0.1/feed.xml
    canonical_url: http://127.0.0.1/feed.xml
    category: media
    language: en
    trust_score: 50
    poll_seconds: 300
    timeout_seconds: 15
    max_response_bytes: 1048576
    enabled: true
    allow_private_networks: false
    allow_localhost: false
    config:
      parser_version: test_v1
""".strip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "scripts/validate_sources.py", str(sources_file), "--strict-contract"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "localhost" in result.stderr or "private" in result.stderr


def test_frontend_performance_detects_chart_leakage(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.js").write_text("console.log('echarts leaked')", encoding="utf-8")
    (assets / "charts.js").write_text("console.log('echarts chunk')", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/frontend_performance_acceptance.py",
            "--dist-dir",
            str(tmp_path),
            "--assert-charts-isolated",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "charts chunk" in result.stderr


def _workflow(name: str) -> dict:
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    return yaml.load(text, Loader=_github_actions_loader())


def _github_actions_loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    for first_char, resolvers in list(Loader.yaml_implicit_resolvers.items()):
        Loader.yaml_implicit_resolvers[first_char] = [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
    return Loader
