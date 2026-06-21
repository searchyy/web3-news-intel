from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_CI_JOBS = {
    "quality",
    "postgres-integration",
    "redis-celery-integration",
    "compose-acceptance",
}
EXPECTED_OPTIONAL_CI_JOBS = {"frontend-quality"}
REQUIRED_WORKFLOW_FILES = {
    "ci.yml",
    "source-adapter-contracts.yml",
    "ai-integration-mock.yml",
    "frontend-performance.yml",
    "live-source-canary.yml",
    "deepseek-test.yml",
}
REQUIRED_WORKFLOW_JOBS = {
    "source-adapter-contracts.yml": {"source-adapter-contracts"},
    "ai-integration-mock.yml": {"ai-integration-mock"},
    "frontend-performance.yml": {"frontend-performance"},
    "live-source-canary.yml": {"live-source-canary"},
    "deepseek-test.yml": {"deepseek-live-test"},
}
SENSITIVE_NAME_RE = re.compile(r"(token|secret|password|api[_-]?key|private[_-]?key)", re.I)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|private[_-]?key)=([^\s]+)"
)


@dataclass(frozen=True)
class CommandResult:
    label: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class AcceptanceError(RuntimeError):
    pass


COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
    ("mypy", [sys.executable, "-m", "mypy", "app", "scripts"]),
    ("unit", [sys.executable, "-m", "pytest", "tests/unit", "-q"]),
    (
        "fixture-integration",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration",
            "-q",
            "-m",
            "not postgres and not redis and not celery and not compose and not live",
        ],
    ),
    (
        "sources",
        [sys.executable, "scripts/validate_sources.py", "sources.yaml"],
    ),
    (
        "source-contracts",
        [
            sys.executable,
            "scripts/validate_sources.py",
            "sources.yaml",
            "--strict-contract",
            "--catalog-dir",
            "source_catalog",
        ],
    ),
    ("security-acceptance", [sys.executable, "scripts/security_acceptance.py"]),
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    results: list[CommandResult] = []
    try:
        validate_workflows(repo_root)
    except AcceptanceError as exc:
        print(f"workflow-contract: FAIL {exc}", file=sys.stderr)
        return 1
    print("workflow-contract: PASS")

    for label, command in COMMANDS:
        printable = " ".join(command)
        print(f"\n==> {label}: {printable}", flush=True)
        result = _run_command(label, command, cwd=repo_root)
        results.append(result)
        _print_captured_output(result)
        if result.returncode != 0:
            _print_summary(results)
            return result.returncode
    _print_summary(results)
    return 0


def validate_workflows(repo_root: Path) -> None:
    path = repo_root / ".github" / "workflows" / "ci.yml"
    try:
        data = _load_github_actions_yaml(path)
    except yaml.YAMLError as exc:
        raise AcceptanceError(f"{path} does not parse as YAML: {exc}") from exc
    except OSError as exc:
        raise AcceptanceError(f"{path} is not readable: {exc}") from exc
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        raise AcceptanceError("ci.yml is missing jobs mapping")
    actual = set(jobs)
    if not REQUIRED_CI_JOBS.issubset(actual):
        raise AcceptanceError(
            "ci.yml is missing required jobs "
            f"{sorted(REQUIRED_CI_JOBS - actual)}; found {sorted(actual)}"
        )
    missing_optional = EXPECTED_OPTIONAL_CI_JOBS - actual
    if missing_optional:
        raise AcceptanceError(
            "ci.yml is missing expected jobs "
            f"{sorted(missing_optional)}; found {sorted(actual)}"
        )
    workflow_dir = repo_root / ".github" / "workflows"
    present = {item.name for item in workflow_dir.glob("*.yml")}
    missing_files = REQUIRED_WORKFLOW_FILES - present
    if missing_files:
        raise AcceptanceError(f"missing required workflow files {sorted(missing_files)}")
    for filename, expected_jobs in REQUIRED_WORKFLOW_JOBS.items():
        workflow_path = workflow_dir / filename
        workflow = _load_github_actions_yaml(workflow_path)
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            raise AcceptanceError(f"{filename} is missing jobs mapping")
        missing_jobs = expected_jobs - set(jobs)
        if missing_jobs:
            raise AcceptanceError(
                f"{filename} is missing required jobs {sorted(missing_jobs)}; "
                f"found {sorted(jobs)}"
            )
        triggers = _workflow_triggers(workflow)
        if filename == "live-source-canary.yml" and "pull_request" in triggers:
            raise AcceptanceError("live-source-canary.yml must not run on pull_request")
        if filename == "deepseek-test.yml" and triggers != {"workflow_dispatch"}:
            raise AcceptanceError("deepseek-test.yml must be workflow_dispatch only")


def _load_github_actions_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=_github_actions_yaml_loader())
    if not isinstance(data, dict):
        raise AcceptanceError(f"{path} must contain a YAML mapping")
    if "on" not in data:
        raise AcceptanceError(f"{path} is missing GitHub Actions 'on' trigger")
    return data


def _github_actions_yaml_loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    for first_char, resolvers in list(Loader.yaml_implicit_resolvers.items()):
        Loader.yaml_implicit_resolvers[first_char] = [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
    return Loader


def _workflow_triggers(data: dict) -> set[str]:
    triggers = data.get("on")
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return {str(item) for item in triggers}
    if isinstance(triggers, dict):
        return {str(key) for key in triggers}
    return set()


def _run_command(label: str, command: list[str], *, cwd: Path) -> CommandResult:
    env = os.environ.copy()
    env.setdefault("PYTEST_FAIL_ON_SKIP", "1")
    try:
        completed = subprocess.run(
            command,
            check=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or command[0]
        return CommandResult(
            label=label,
            command=command,
            returncode=127,
            stderr=f"executable not found: {missing}",
        )
    return CommandResult(
        label=label,
        command=command,
        returncode=completed.returncode,
        stdout=_redact(completed.stdout or ""),
        stderr=_redact(completed.stderr or ""),
    )


def _print_captured_output(result: CommandResult) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


def _redact(text: str) -> str:
    redacted = text
    for value in _sensitive_env_values():
        redacted = redacted.replace(value, "<redacted>")
    return SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)


def _sensitive_env_values() -> set[str]:
    values: set[str] = set()
    for name, value in os.environ.items():
        if value and len(value) >= 4 and SENSITIVE_NAME_RE.search(name):
            values.add(value)
    return values


def _print_summary(results: list[CommandResult]) -> None:
    print("\nPre-push acceptance summary:")
    for result in results:
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"- {result.label}: {status} exit={result.returncode}")


if __name__ == "__main__":
    raise SystemExit(main())
