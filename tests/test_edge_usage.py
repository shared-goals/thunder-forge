"""Exercise the /usage endpoints over the real edge HTTP handler."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from thunder_forge.cluster import edge
from thunder_forge.cluster.config import OperationConfig


def _edge_config(tmp_path: Path, *, token: str | None) -> tuple[edge.EdgeProxyConfig, Path, Path]:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:00+00:00",
                        "client_id": "alice",
                        "model": "agent",
                        "latency_ms": 2500,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:01+00:00",
                        "client_id": "bob",
                        "model": "agent",
                        "latency_ms": 12500,
                        "olla_endpoint": "msm2-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-03T09:00:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 100,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
            ]
        )
        + "\n"
    )
    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    node_metrics.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:16:00+00:00",
                "node_id": "msm1",
                "health_ok": True,
                "models_ok": True,
                "status_ok": True,
                "hot_loaded_models": ["agent"],
                "hot_loaded_count": 1,
                "active_requests": 2,
                "waiting_requests": 1,
                "loaded_models": [{"id": "agent", "actual_size": 1073741824, "idle_seconds": 30}],
                "errors": [],
            }
        )
        + "\n"
    )
    config = edge.EdgeProxyConfig(
        olla_base_url="http://olla.test",
        clients_by_key={},
        cluster_config=SimpleNamespace(operations=OperationConfig()),
        usage_admin_token=token,
        usage_access_log_path=access_log,
        usage_node_metrics_path=node_metrics,
    )
    return config, access_log, node_metrics


@pytest.fixture
def get(monkeypatch):
    def run(
        config: edge.EdgeProxyConfig, path: str, *, token: str | None = None, method: str = "GET"
    ) -> tuple[int, str, str]:
        handlers: list[object] = []

        def server(_address, handler):
            handlers.append(handler)
            return SimpleNamespace(serve_forever=lambda: None)

        monkeypatch.setattr(edge, "ThreadingHTTPServer", server)
        monkeypatch.setattr(
            edge.httpx,
            "Client",
            lambda **kwargs: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        edge.serve_edge_proxy(host="127.0.0.1", port=0, config=config)
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        request = f"{method} {path} HTTP/1.0\r\n{auth}\r\n".encode()
        client, connection = socket.socketpair()
        errors: list[BaseException] = []

        def handle():
            try:
                handlers[0](connection, ("127.0.0.1", 1234), None)
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        with client:
            client.settimeout(5)
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            thread = threading.Thread(target=handle, daemon=True)
            thread.start()
            try:
                with client.makefile("rb") as response:
                    wire = response.read()
            finally:
                thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        head, _, body = wire.partition(b"\r\n\r\n")
        status = int(head.split(b" ")[1])
        return status, head.decode(), body.decode()

    return run


def test_usage_endpoints_stay_hidden_without_admin_token(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token=None)

    for path in ("/usage", "/usage/report?period=2026-06-02", "/usage/events?period=2026-06-02"):
        status, _, body = get(config, path)
        assert status == 404
        assert json.loads(body) == {"error": "not_found"}


def test_usage_endpoints_reject_missing_and_wrong_token(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    assert get(config, "/usage/report?period=2026-06-02")[0] == 401
    assert get(config, "/usage/report?period=2026-06-02", token="wrong")[0] == 401
    # A query token would leak into proxy logs, so it is not accepted at all.
    assert get(config, "/usage/report?period=2026-06-02&token=admin-secret")[0] == 401


def test_usage_report_returns_latency_concurrency_and_node_metrics(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    status, head, body = get(config, "/usage/report?period=2026-06-02", token="admin-secret")

    assert status == 200
    assert "application/json" in head
    payload = json.loads(body)
    assert payload["period"] == "2026-06-02"
    assert payload["requests"]["total"] == 2
    assert payload["latency_ms"]["by_hour"]["08"] == {"count": 2, "p50": 2500, "p95": 12500, "max": 12500}
    assert payload["concurrency"]["peak_by_hour"]["08"] == 2
    assert payload["node_metrics"]["load_by_hour"]["08"]["msm1"] == {
        "samples": 1,
        "active_max": 2,
        "active_avg": 2.0,
        "waiting_max": 1,
    }
    assert payload["node_metrics"]["model_memory"]["msm1"]["agent"] == {
        "actual_size": 1073741824,
        "idle_seconds": 30,
    }


def test_usage_events_download_is_period_scoped(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    status, head, body = get(config, "/usage/events?period=2026-06-02", token="admin-secret")

    assert status == 200
    assert 'attachment; filename="tf-edge-access-2026-06-02.jsonl"' in head
    records = [json.loads(line) for line in body.splitlines() if line.strip()]
    assert [record["client_id"] for record in records] == ["alice", "bob"]


def test_usage_page_is_anonymous_but_data_is_not(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    # A browser navigation cannot send an Authorization header, so the page itself is public.
    status, head, body = get(config, "/usage")
    assert status == 200
    assert "text/html" in head
    assert "Thunder Forge usage" in body

    assert get(config, "/usage/report?period=2026-06-02")[0] == 401
    assert get(config, "/usage/events?period=2026-06-02")[0] == 401

    status, _, body = get(config, "/usage/report?period=02-06-2026", token="admin-secret")
    assert status == 400
    assert json.loads(body) == {"error": "invalid_period"}


def test_usage_page_uses_the_vendored_chart_asset(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    status, _, body = get(config, "/usage")
    assert status == 200
    assert "cdn.jsdelivr" not in body
    assert 'src="/usage/assets/chart.umd.min.js"' in body

    status, head, script = get(config, "/usage/assets/chart.umd.min.js")
    assert status == 200
    assert "application/javascript" in head
    assert "Chart" in script


def test_usage_assets_are_hidden_and_not_traversable(tmp_path: Path, get) -> None:
    hidden, _, _ = _edge_config(tmp_path, token=None)
    assert get(hidden, "/usage/assets/chart.umd.min.js")[0] == 404

    config, _, _ = _edge_config(tmp_path, token="admin-secret")
    assert get(config, "/usage/assets/..%2Fedge.py")[0] == 404
    assert get(config, "/usage/assets/missing.js")[0] == 404


def test_unknown_usage_paths_and_methods_stay_invisible(tmp_path: Path, get) -> None:
    disabled, _, _ = _edge_config(tmp_path, token=None)
    # A wrong method must not reveal that the endpoints exist.
    assert get(disabled, "/usage", method="POST")[0] == 404
    assert get(disabled, "/usage/report?period=2026-06-02", method="POST")[0] == 404

    config, _, _ = _edge_config(tmp_path, token="admin-secret")
    # Data paths authorize before checking the method, so POST cannot confirm they exist.
    assert get(config, "/usage/report?period=2026-06-02", method="POST")[0] == 401
    assert get(config, "/usage/report?period=2026-06-02", method="POST", token="admin-secret")[0] == 405
    assert get(config, "/usage/nope", token="admin-secret")[0] == 404
    assert get(config, "/usage/nope?period=02-06-2026", token="admin-secret")[0] == 404


def test_usage_events_can_download_node_samples(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    status, head, body = get(config, "/usage/events?period=2026-06-02&source=node-metrics", token="admin-secret")

    assert status == 200
    assert 'attachment; filename="tf-node-metrics-2026-06-02.jsonl"' in head
    assert json.loads(body.splitlines()[0])["node_id"] == "msm1"
    assert get(config, "/usage/events?period=2026-06-02&source=logs", token="admin-secret")[0] == 400
    # Downloading every retained day at once would buffer the whole file.
    assert get(config, "/usage/events?period=all", token="admin-secret")[0] == 400


def test_usage_report_survives_a_non_ascii_token(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    # hmac.compare_digest raises TypeError on non-ASCII str, so it must compare bytes.
    assert get(config, "/usage/report?period=2026-06-02", token="\u00fcber-token")[0] == 401


def test_usage_events_refuse_an_oversized_log_before_reading_it(tmp_path: Path, get, monkeypatch) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")
    monkeypatch.setattr(edge, "USAGE_LOG_MAX_BYTES", 16)

    status, _, body = get(config, "/usage/events?period=2026-06-02", token="admin-secret")

    assert status == 413
    assert json.loads(body) == {"error": "usage_events_too_large"}


def test_usage_has_no_bare_assets_alias(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    # Only the documented /usage and /usage/assets/<file> routes exist.
    status, _, _ = get(config, "/usage/assets", token="admin-secret")
    assert status == 404


def test_usage_report_filters_by_user_without_exposing_keys(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")

    status, _, body = get(config, "/usage/report?period=2026-06-02&user=alice", token="admin-secret")
    assert status == 200
    payload = json.loads(body)
    assert payload["users"]["available"] == ["alice", "bob"]
    assert payload["users"]["selected"] == ["alice"]
    # No edge keys are configured in this fixture, so the configured roster is empty.
    assert payload["users"]["known"] == []
    assert payload["requests"]["total"] == 1
    assert payload["requests"]["by_user"] == {"alice": 1}
    # Client identities only: the admin token and any key material stay out of the payload.
    assert config.usage_admin_token not in body

    # Repeated and comma-separated values are both accepted.
    both = json.loads(get(config, "/usage/report?period=2026-06-02&user=alice,bob", token="admin-secret")[2])
    assert both["users"]["selected"] == ["alice", "bob"]
    assert both["requests"]["total"] == 2
    repeated = json.loads(
        get(config, "/usage/report?period=2026-06-02&user=alice&user=bob", token="admin-secret")[2]
    )
    assert repeated["requests"]["total"] == 2

    unknown = json.loads(get(config, "/usage/report?period=2026-06-02&user=nobody", token="admin-secret")[2])
    assert unknown["requests"]["total"] == 0
    assert unknown["users"]["available"] == ["alice", "bob"]


def test_usage_report_lists_configured_clients_without_traffic(tmp_path: Path, get) -> None:
    config, _, _ = _edge_config(tmp_path, token="admin-secret")
    config.clients_by_key = {
        "key-alice": edge.EdgeClient(client_id="alice"),
        "key-quiet": edge.EdgeClient(client_id="quiet-client"),
    }

    payload = json.loads(get(config, "/usage/report?period=2026-06-02", token="admin-secret")[2])

    # The selector must offer a client that sent nothing today.
    assert payload["users"]["known"] == ["alice", "quiet-client"]
    assert payload["users"]["available"] == ["alice", "bob"]
    # Keys themselves never leave the edge.
    assert "key-alice" not in json.dumps(payload)


def test_usage_report_reports_a_missing_access_log(tmp_path: Path, get) -> None:
    config, access_log, _ = _edge_config(tmp_path, token="admin-secret")
    access_log.unlink()

    status, _, body = get(config, "/usage/report?period=2026-06-02", token="admin-secret")

    assert status == 503
    assert json.loads(body) == {"error": "usage_access_log_unavailable"}
