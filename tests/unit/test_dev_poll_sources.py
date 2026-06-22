from __future__ import annotations

import scripts.dev_poll_sources as dev_poll_sources


def test_dev_poll_sources_ignores_proxy_environment(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, path: str):
            assert path == "/dev/run-source/binance_listing"
            return FakeResponse()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": "success", "items": 1}

    monkeypatch.setattr(dev_poll_sources.httpx, "Client", FakeClient)

    results = dev_poll_sources.run_once(
        "http://127.0.0.1:59134",
        ["binance_listing"],
        timeout=1,
    )

    assert captured["trust_env"] is False
    assert results[0]["status"] == "success"
