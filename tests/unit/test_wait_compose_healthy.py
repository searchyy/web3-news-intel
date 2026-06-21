from __future__ import annotations

import json
import subprocess

import scripts.wait_compose_healthy as waiter


def test_all_required_services_immediately_healthy(monkeypatch, capsys) -> None:
    monkeypatch.setattr(waiter.subprocess, "run", _runner([_services_json("healthy")]))
    assert waiter.main(["--timeout", "0"]) == 0
    assert "All compose services are healthy" in capsys.readouterr().out


def test_starting_services_become_healthy_after_polling(monkeypatch) -> None:
    clock = _Clock([0, 1, 2])
    monkeypatch.setattr(waiter.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(waiter.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        waiter.subprocess,
        "run",
        _runner([_services_json("starting"), _services_json("healthy")]),
    )
    assert waiter.main(["--timeout", "10"]) == 0
    assert clock.sleeps == [2]


def test_unhealthy_service_returns_failure_on_timeout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(waiter.subprocess, "run", _runner([_services_json("unhealthy")]))
    assert waiter.main(["--timeout", "0"]) == 1
    assert "Timed out waiting for compose health" in capsys.readouterr().err


def test_missing_required_service_returns_failure(monkeypatch, capsys) -> None:
    services = _services("healthy")
    services = [service for service in services if service["Service"] != "worker"]
    monkeypatch.setattr(waiter.subprocess, "run", _runner([json.dumps(services)]))
    assert waiter.main(["--timeout", "0"]) == 1
    assert "worker" in capsys.readouterr().err


def test_custom_required_services_are_checked(monkeypatch, capsys) -> None:
    services = _services("healthy") + [
        {"Service": "mock-deepseek", "Health": "healthy"},
        {"Service": "mock-feishu", "Health": "healthy"},
    ]
    monkeypatch.setattr(waiter.subprocess, "run", _runner([json.dumps(services)]))
    assert (
        waiter.main(
            [
                "--timeout",
                "0",
                "--services",
                "postgres,redis,mock-deepseek,mock-feishu",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "mock-deepseek" in output
    assert "mock-feishu" in output


def test_docker_executable_missing_returns_clear_failure(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError(2, "missing", "docker")

    monkeypatch.setattr(waiter.subprocess, "run", fake_run)
    assert waiter.main(["--timeout", "0"]) == 1
    assert "docker executable not found" in capsys.readouterr().err


def test_docker_compose_command_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        waiter.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 2, stdout="", stderr="compose failed"
        ),
    )
    assert waiter.main(["--timeout", "0"]) == 1
    assert "docker compose ps failed: compose failed" in capsys.readouterr().err


def test_empty_output_times_out(monkeypatch, capsys) -> None:
    monkeypatch.setattr(waiter.subprocess, "run", _runner([""]))
    assert waiter.main(["--timeout", "0"]) == 1
    assert "Timed out waiting" in capsys.readouterr().err


def test_malformed_json_output_returns_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(waiter.subprocess, "run", _runner(["{not-json"]))
    assert waiter.main(["--timeout", "0"]) == 1
    assert "invalid docker compose JSON output" in capsys.readouterr().err


def test_timeout_behavior_uses_bounded_polling(monkeypatch) -> None:
    clock = _Clock([0, 1, 3])
    monkeypatch.setattr(waiter.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(waiter.time, "sleep", clock.sleep)
    calls = _runner([_services_json("starting"), _services_json("starting")])
    monkeypatch.setattr(waiter.subprocess, "run", calls)
    assert waiter.main(["--timeout", "2"]) == 1
    assert calls.count == 2
    assert clock.sleeps == [2]


def test_timeout_boundary_checks_once(monkeypatch) -> None:
    calls = _runner([_services_json("healthy")])
    monkeypatch.setattr(waiter.subprocess, "run", calls)
    assert waiter.main(["--timeout", "0"]) == 0
    assert calls.count == 1


def test_single_object_compose_json_format(monkeypatch) -> None:
    service = {"Service": "postgres", "Health": "healthy"}
    monkeypatch.setattr(waiter.subprocess, "run", _runner([json.dumps(service)]))
    services = waiter._compose_services()
    assert services == [service]


def test_ndjson_compose_json_format(monkeypatch) -> None:
    raw = "\n".join(json.dumps(service) for service in _services("healthy"))
    monkeypatch.setattr(waiter.subprocess, "run", _runner([raw]))
    assert len(waiter._compose_services()) == len(waiter.REQUIRED_SERVICES)


def test_ctrl_c_cancellation_exits_cleanly(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(waiter.subprocess, "run", fake_run)
    assert waiter.main(["--timeout", "10"]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_no_infinite_retry(monkeypatch) -> None:
    clock = _Clock([0, 100])
    monkeypatch.setattr(waiter.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(waiter.time, "sleep", clock.sleep)
    calls = _runner([_services_json("starting")])
    monkeypatch.setattr(waiter.subprocess, "run", calls)
    assert waiter.main(["--timeout", "1"]) == 1
    assert calls.count == 1


class _runner:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.count = 0

    def __call__(self, command, **kwargs):
        assert command == ["docker", "compose", "ps", "--format", "json"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        output = self.outputs[min(self.count, len(self.outputs) - 1)]
        self.count += 1
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class _Clock:
    def __init__(self, ticks: list[float]):
        self.ticks = ticks
        self.index = 0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        value = self.ticks[min(self.index, len(self.ticks) - 1)]
        self.index += 1
        return value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _services_json(health: str) -> str:
    return json.dumps(_services(health))


def _services(health: str) -> list[dict[str, str]]:
    return [{"Service": service, "Health": health} for service in waiter.REQUIRED_SERVICES]
