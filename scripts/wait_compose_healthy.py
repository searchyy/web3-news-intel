from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

REQUIRED_SERVICES = (
    "postgres",
    "redis",
    "api",
    "ai-worker",
    "report-worker",
    "fetch-worker",
    "pipeline-worker",
    "scheduler",
    "frontend",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--services",
        default=",".join(REQUIRED_SERVICES),
        help="Comma-separated Compose service names that must be healthy.",
    )
    args = parser.parse_args(argv)
    required_services = tuple(
        service.strip() for service in args.services.split(",") if service.strip()
    )
    if not required_services:
        print("No Compose services were requested", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.timeout
    last_status: dict[str, str] = {}
    try:
        while True:
            services = _compose_services()
            last_status = _status_by_service(services)
            missing = [service for service in required_services if service not in last_status]
            unhealthy = [
                service
                for service in required_services
                if last_status.get(service) not in {"healthy"}
            ]
            if not missing and not unhealthy:
                print("All compose services are healthy:")
                for service in required_services:
                    print(f"- {service}: {last_status[service]}")
                return 0
            if time.monotonic() >= deadline:
                _emit_github_error(last_status, missing=missing, unhealthy=unhealthy)
                print(
                    "Timed out waiting for compose health: "
                    f"{last_status}; missing={missing}; unhealthy={unhealthy}",
                    file=sys.stderr,
                )
                return 1
            print(f"Waiting for compose health: {last_status}; missing={missing}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("Interrupted while waiting for compose health", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"Compose health check failed: {exc}", file=sys.stderr)
        return 1


def _compose_services() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or "docker"
        raise RuntimeError(f"docker executable not found: {missing}") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"docker compose ps failed: {message}")
    raw = completed.stdout.strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
    except json.JSONDecodeError:
        services: list[dict[str, Any]] = []
        try:
            for line in raw.splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, dict):
                        services.append(item)
                    else:
                        raise RuntimeError("docker compose JSON item is not an object")
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid docker compose JSON output") from exc
        return services
    raise RuntimeError("unexpected docker compose ps JSON output")


def _emit_github_error(
    last_status: dict[str, str], *, missing: list[str], unhealthy: list[str]
) -> None:
    payload = json.dumps(
        {"status": last_status, "missing": missing, "unhealthy": unhealthy},
        sort_keys=True,
    )
    sanitized = payload.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title=Compose health timeout::{sanitized}", file=sys.stderr)


def _status_by_service(services: list[dict[str, Any]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for service in services:
        name = str(service.get("Service") or "")
        if not name:
            continue
        health = service.get("Health")
        state = str(service.get("State") or "").lower()
        statuses[name] = str(health or state).lower()
    return statuses


if __name__ == "__main__":
    raise SystemExit(main())
