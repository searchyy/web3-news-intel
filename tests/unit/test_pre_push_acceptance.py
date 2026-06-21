from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

import scripts.pre_push_acceptance as acceptance


def test_all_acceptance_commands_succeed(monkeypatch, capsys) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["cwd"], kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 0
    assert len(calls) == len(acceptance.COMMANDS)
    assert all(call[1] == repo for call in calls)
    assert all(call[2]["PYTEST_FAIL_ON_SKIP"] == "1" for call in calls)
    output = capsys.readouterr()
    assert "workflow-contract: PASS" in output.out
    assert "ruff: PASS exit=0" in output.out


def test_one_command_fails_and_script_returns_nonzero(monkeypatch, capsys) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)

    def fake_run(command, **_kwargs):
        if command[2:4] == ["ruff", "check"]:
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="ruff failed\n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 7
    output = capsys.readouterr()
    assert "ruff failed" in output.err
    assert "ruff: FAIL exit=7" in output.out
    assert "mypy:" not in output.out


def test_missing_executable_returns_clear_nonzero(monkeypatch, capsys) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)
    monkeypatch.setattr(acceptance, "COMMANDS", (("missing", ["missing-python"]),))

    def fake_run(command, **_kwargs):
        raise FileNotFoundError(2, "missing", command[0])

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 127
    output = capsys.readouterr()
    assert "executable not found: missing-python" in output.err


def test_command_stdout_and_stderr_are_captured(monkeypatch, capsys) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)
    monkeypatch.setattr(acceptance, "COMMANDS", (("cmd", [sys.executable, "-c", "ok"]),))

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="stdout text\n", stderr="stderr text\n"
        )

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 0
    output = capsys.readouterr()
    assert "stdout text" in output.out
    assert "stderr text" in output.err


def test_required_ci_workflow_job_names_are_present() -> None:
    repo = _repo()
    acceptance.validate_workflows(repo)


def test_missing_required_job_name_fails_acceptance() -> None:
    repo = _repo(jobs=("quality",))
    with pytest.raises(acceptance.AcceptanceError, match="missing required jobs"):
        acceptance.validate_workflows(repo)


def test_yaml_parsing_failure_fails_acceptance() -> None:
    repo = _workspace() / "repo"
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: [unterminated\n", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceError, match="does not parse"):
        acceptance.validate_workflows(repo)


def test_test_skips_are_rejected_when_fail_on_skip_is_enabled(monkeypatch) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)
    seen_env: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        seen_env.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 1, stdout="s [100%]\n", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 1
    assert seen_env[0]["PYTEST_FAIL_ON_SKIP"] == "1"


def test_paths_work_independently_of_current_working_directory(
    monkeypatch,
) -> None:
    workspace = _workspace()
    repo = _repo(workspace)
    other = workspace / "other"
    other.mkdir()
    _point_script_at_repo(monkeypatch, repo)
    monkeypatch.chdir(other)
    seen_cwd: list[Path] = []

    def fake_run(command, **kwargs):
        seen_cwd.append(kwargs["cwd"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 0
    assert seen_cwd
    assert set(seen_cwd) == {repo}


def test_python_invocation_is_portable() -> None:
    assert all(command[0] == sys.executable for _, command in acceptance.COMMANDS)
    assert all(isinstance(command, list) for _, command in acceptance.COMMANDS)


def test_secrets_and_environment_tokens_are_not_printed(
    monkeypatch, capsys
) -> None:
    repo = _repo()
    _point_script_at_repo(monkeypatch, repo)
    monkeypatch.setenv("SERVICE_TOKEN", "super-secret-token")
    monkeypatch.setattr(acceptance, "COMMANDS", (("cmd", [sys.executable, "-c", "ok"]),))

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="SERVICE_TOKEN=super-secret-token token=abc123\n",
            stderr="password=letmein\n",
        )

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 0
    output = capsys.readouterr()
    combined = output.out + output.err
    assert "super-secret-token" not in combined
    assert "abc123" not in combined
    assert "letmein" not in combined
    assert "<redacted>" in combined


def _repo(workspace: Path | None = None, *, jobs: tuple[str, ...] | None = None) -> Path:
    repo = (workspace or _workspace()) / "repo"
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    job_names = jobs or tuple(
        sorted(acceptance.REQUIRED_CI_JOBS | acceptance.EXPECTED_OPTIONAL_CI_JOBS)
    )
    workflow.write_text(_workflow(job_names), encoding="utf-8")
    return repo


def _workspace() -> Path:
    root = Path(__file__).resolve().parents[1] / "_acceptance_tmp" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _point_script_at_repo(monkeypatch, repo: Path) -> None:
    script_path = repo / "scripts" / "pre_push_acceptance.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(acceptance, "__file__", str(script_path))


def _workflow(jobs: tuple[str, ...]) -> str:
    rendered_jobs = "\n".join(
        f"  {job}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true"
        for job in jobs
    )
    return f"""name: CI
permissions:
  contents: read
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
jobs:
{rendered_jobs}
"""
