from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
CANARY_PATH = ROOT / ".github" / "workflows" / "live-source-canary.yml"
REQUIRED_JOBS = {
    "quality",
    "postgres-integration",
    "redis-celery-integration",
    "compose-acceptance",
}


def test_ci_workflow_yaml_parses() -> None:
    workflow = _workflow(CI_PATH)
    assert "on" in workflow
    assert "jobs" in workflow


def test_ci_required_jobs_exist_exactly() -> None:
    jobs = _workflow(CI_PATH)["jobs"]
    assert set(jobs) == REQUIRED_JOBS


def test_ci_required_jobs_use_ubuntu_latest() -> None:
    jobs = _workflow(CI_PATH)["jobs"]
    assert all(jobs[name]["runs-on"] == "ubuntu-latest" for name in REQUIRED_JOBS)


def test_no_required_job_has_continue_on_error() -> None:
    jobs = _workflow(CI_PATH)["jobs"]
    for name in REQUIRED_JOBS:
        assert jobs[name].get("continue-on-error") not in {True, "true", "True"}


def test_postgres_integration_has_postgres_service_and_healthcheck() -> None:
    job = _workflow(CI_PATH)["jobs"]["postgres-integration"]
    postgres = job["services"]["postgres"]
    assert "postgres" in postgres["image"]
    assert "pg_isready" in postgres["options"]


def test_redis_celery_integration_has_redis_service_and_healthcheck() -> None:
    job = _workflow(CI_PATH)["jobs"]["redis-celery-integration"]
    redis = job["services"]["redis"]
    assert "redis" in redis["image"]
    assert "redis-cli ping" in redis["options"]


def test_real_service_jobs_set_fail_on_skip() -> None:
    jobs = _workflow(CI_PATH)["jobs"]
    assert jobs["postgres-integration"]["env"]["PYTEST_FAIL_ON_SKIP"] == "1"
    assert jobs["redis-celery-integration"]["env"]["PYTEST_FAIL_ON_SKIP"] == "1"


def test_compose_acceptance_runs_required_compose_commands() -> None:
    commands = _job_run_commands(_workflow(CI_PATH)["jobs"]["compose-acceptance"])
    assert any("docker compose config --quiet" in command for command in commands)
    assert any("docker compose build" in command for command in commands)
    assert any("docker compose up -d" in command for command in commands)
    assert any("python scripts/wait_compose_healthy.py" in command for command in commands)


def test_compose_acceptance_tears_down_even_after_failure() -> None:
    steps = _workflow(CI_PATH)["jobs"]["compose-acceptance"]["steps"]
    down_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and "docker compose down -v --remove-orphans" in step.get("run", "")
    ]
    assert down_steps
    assert down_steps[0].get("if") == "always()"


def test_workflow_has_least_privilege_permissions() -> None:
    workflow = _workflow(CI_PATH)
    assert workflow["permissions"] == {"contents": "read"}
    canary = _workflow(CANARY_PATH)
    assert canary["permissions"] == {"contents": "read"}


def test_workflows_have_no_hardcoded_secret_values() -> None:
    text = CI_PATH.read_text(encoding="utf-8") + "\n" + CANARY_PATH.read_text(encoding="utf-8")
    forbidden = [
        r"ghp_[A-Za-z0-9_]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----",
    ]
    for pattern in forbidden:
        assert re.search(pattern, text) is None


def test_workflows_have_no_absolute_local_filesystem_paths() -> None:
    text = CI_PATH.read_text(encoding="utf-8") + "\n" + CANARY_PATH.read_text(encoding="utf-8")
    assert re.search(r"[A-Za-z]:\\", text) is None
    assert "/Users/" not in text
    assert "/home/" not in text


def test_live_source_canary_yaml_parses() -> None:
    workflow = _workflow(CANARY_PATH)
    assert "on" in workflow
    assert "jobs" in workflow


def test_live_source_canary_has_dispatch_and_schedule_only() -> None:
    triggers = _workflow(CANARY_PATH)["on"]
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers
    assert "pull_request" not in triggers


def test_live_source_canary_has_timeout_minutes() -> None:
    job = _workflow(CANARY_PATH)["jobs"]["live-source-canary"]
    assert int(job["timeout-minutes"]) > 0


def test_live_source_canary_does_not_expose_secrets_in_commands() -> None:
    commands = _job_run_commands(_workflow(CANARY_PATH)["jobs"]["live-source-canary"])
    joined = "\n".join(commands).lower()
    assert "secrets." not in joined
    assert "token=" not in joined
    assert "password=" not in joined


def test_canary_is_separate_from_deterministic_release_gate_tests() -> None:
    workflow = _workflow(CANARY_PATH)
    commands = _job_run_commands(workflow["jobs"]["live-source-canary"])
    joined = "\n".join(commands)
    assert "scripts/live_source_canary.py" in joined
    assert "pytest tests/unit" not in joined
    assert "pytest tests/integration" not in joined
    assert "pre_push_acceptance.py" not in joined


def _workflow(path: Path) -> dict[str, Any]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_github_actions_loader())
    assert isinstance(data, dict)
    return data


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


def _job_run_commands(job: dict[str, Any]) -> list[str]:
    return [
        str(step["run"])
        for step in job.get("steps", [])
        if isinstance(step, dict) and "run" in step
    ]
