"""Tests for the minimal Thunder Forge edge contract."""

import json

import httpx

from thunder_forge.cluster.edge import (
    EdgeAccessLog,
    EdgeClient,
    EdgeProxyConfig,
    authenticate_edge_request,
    build_edge_access_log,
    build_edge_clients_from_env,
    edge_api_key_from_env,
    ensure_edge_api_keys,
    ensure_olla_session_id,
    load_edge_clients_from_env,
    parse_edge_users_json,
    proxy_edge_request,
    rewrite_openai_path,
    summarize_edge_usage,
)


def test_edge_auth_rejects_missing_and_invalid_api_keys() -> None:
    clients = {"dev-secret": EdgeClient(client_id="client-a")}

    missing = authenticate_edge_request(None, clients)
    invalid = authenticate_edge_request("Bearer wrong-secret", clients)

    assert missing.allowed is False
    assert missing.status_code == 401
    assert missing.client_id == ""
    assert invalid.allowed is False
    assert invalid.status_code == 401
    assert invalid.client_id == ""


def test_edge_auth_maps_valid_bearer_token_to_client_id_without_exposing_secret() -> None:
    clients = {"dev-secret": EdgeClient(client_id="client-a")}

    result = authenticate_edge_request("Bearer dev-secret", clients)

    assert result.allowed is True
    assert result.status_code == 200
    assert result.client_id == "client-a"
    assert "dev-secret" not in repr(result)


def test_edge_users_and_multi_client_loader() -> None:
    env = {
        "TF_USER_CLIENT_A": "secret-a",
        "TF_USER_CLIENT_B": "secret-b",
        "OTHER_KEY": "ignored",
    }

    assert edge_api_key_from_env(env=env, client_id="client_a") == ("TF_USER_CLIENT_A", "secret-a")

    clients = load_edge_clients_from_env(env=env)

    assert clients["secret-a"].client_id == "client_a"
    assert clients["secret-b"].client_id == "client_b"
    assert "" not in clients


def test_edge_users_json_parses_json_blob() -> None:
    assert parse_edge_users_json('{"client-a":"secret-a","client-b":"secret-b","empty":""}') == {
        "client-a": "secret-a",
        "client-b": "secret-b",
    }


def test_build_edge_clients_reads_only_user_prefixed_keys() -> None:
    clients = build_edge_clients_from_env(
        env={"TF_USER_CLIENT_A": "secret-a", "OTHER_KEY": "ignored-secret"},
    )

    assert clients == {"secret-a": EdgeClient(client_id="client_a")}


def test_ensure_edge_api_keys_creates_missing_keys_without_overwriting_existing(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=keep-me\nTF_USER_CLIENT_A=existing-a\n")

    result = ensure_edge_api_keys(env_file=env_file, clients=["client_a", "client_b"])
    content = env_file.read_text()

    assert result.env_file == str(env_file)
    assert [(key.client_id, key.env_name, key.status) for key in result.keys] == [
        ("client_a", "TF_USER_CLIENT_A", "present"),
        ("client_b", "TF_USER_CLIENT_B", "created"),
    ]
    assert "HF_TOKEN=keep-me" in content
    assert "TF_USER_CLIENT_A=existing-a" in content
    assert "TF_USER_CLIENT_B=" in content
    assert "TF_USERS" not in content
    assert "existing-a" not in repr(result)


def test_rewrite_openai_path_maps_root_v1_to_olla_provider_path() -> None:
    assert rewrite_openai_path("/v1/models") == "/olla/openai-compatible/v1/models"
    assert rewrite_openai_path("/v1/chat/completions") == "/olla/openai-compatible/v1/chat/completions"


def test_rewrite_openai_path_rejects_non_openai_paths() -> None:
    try:
        rewrite_openai_path("/internal/health")
    except ValueError as exc:
        assert "only /v1/* paths" in str(exc)
    else:
        raise AssertionError("expected non-/v1 path to be rejected")


def test_session_id_is_preserved_or_generated_without_using_api_key() -> None:
    provided = ensure_olla_session_id({"X-Olla-Session-ID": "session-123"}, request_id="req-1", client_id="client-a")
    generated = ensure_olla_session_id({}, request_id="req-2", client_id="client-a")

    assert provided.value == "session-123"
    assert provided.generated is False
    assert generated.generated is True
    assert generated.value.startswith("tf-client-a-req-2")
    assert "secret" not in generated.value


def test_access_log_contains_accounting_fields_and_no_api_key() -> None:
    record = build_edge_access_log(
        request_id="req-1",
        client_id="client-a",
        path="/v1/chat/completions",
        model="qwen3-1.7b-omlx-msm3-test",
        status_code=200,
        latency_ms=42,
        olla_endpoint="msm3-omlx-live",
        api_key="dev-secret",
    )

    assert isinstance(record, EdgeAccessLog)
    payload = record.to_json_dict()
    assert "timestamp" in payload
    assert isinstance(payload["timestamp"], str)
    assert payload["request_id"] == "req-1"
    assert payload["client_id"] == "client-a"
    assert payload["model"] == "qwen3-1.7b-omlx-msm3-test"
    assert payload["status_code"] == 200
    assert payload["latency_ms"] == 42
    assert payload["olla_endpoint"] == "msm3-omlx-live"
    assert "api_key" not in payload
    assert "dev-secret" not in str(payload)


def test_proxy_edge_request_rejects_missing_and_invalid_auth_without_calling_olla() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"unexpected": True})

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
    )

    missing = proxy_edge_request(
        method="GET",
        path="/v1/models",
        headers={},
        body=b"",
        config=config,
        transport=httpx.MockTransport(handler),
    )
    invalid = proxy_edge_request(
        method="GET",
        path="/v1/models",
        headers={"Authorization": "Bearer wrong-secret"},
        body=b"",
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert requests == []
    assert missing.body == b'{"error":"unauthorized"}'
    assert invalid.body == b'{"error":"unauthorized"}'


def test_proxy_edge_request_rejects_streaming_chat_without_calling_olla_or_logging_secrets() -> None:
    forwarded: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(200, json={"unexpected": True})

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
    )

    result = proxy_edge_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"Authorization": "Bearer dev-secret", "Content-Type": "application/json"},
        body=json.dumps(
            {
                "model": "qwen3-1.7b-omlx-msm3-test",
                "messages": [{"role": "user", "content": "do not log me"}],
                "stream": True,
            }
        ).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 501
    assert json.loads(result.body) == {
        "error": "streaming_not_implemented",
        "message": "TF edge is a non-streaming proxy; send stream=false or omit stream.",
    }
    assert forwarded == []
    assert all("dev-secret" not in entry for entry in logs)
    assert all("do not log me" not in entry for entry in logs)


def test_proxy_edge_request_rewrites_path_forwards_session_and_logs_without_secret() -> None:
    forwarded: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(
            200,
            headers={"X-Olla-Endpoint": "msm3-omlx-live"},
            json={"id": "chatcmpl-1", "choices": [{"message": {"content": "pong"}}]},
        )

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
    )

    result = proxy_edge_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"Authorization": "Bearer dev-secret", "X-Olla-Session-ID": "session-123"},
        body=json.dumps({"model": "qwen3-1.7b-omlx-msm3-test", "messages": []}).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    assert result.body == b'{"id":"chatcmpl-1","choices":[{"message":{"content":"pong"}}]}'
    assert result.headers["X-Olla-Endpoint"] == "msm3-omlx-live"
    assert len(forwarded) == 1
    assert forwarded[0].method == "POST"
    assert forwarded[0].url == "http://olla.local:40115/olla/openai-compatible/v1/chat/completions"
    assert forwarded[0].headers["X-Olla-Session-ID"] == "session-123"
    assert "authorization" not in forwarded[0].headers
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["path"] == "/v1/chat/completions"
    assert logged["model"] == "qwen3-1.7b-omlx-msm3-test"
    assert logged["status_code"] == 200
    assert logged["olla_endpoint"] == "msm3-omlx-live"
    assert "dev-secret" not in logs[0]


def test_summarize_edge_usage_groups_requests_by_client(tmp_path) -> None:
    log_path = tmp_path / "tf-edge-access.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "client_id": "client-a",
                        "path": "/v1/chat/completions",
                        "model": "memory",
                        "status_code": 200,
                        "latency_ms": 10,
                        "olla_endpoint": "msm3-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "client_id": "client-a",
                        "path": "/v1/chat/completions",
                        "model": "agent",
                        "status_code": 500,
                        "latency_ms": 30,
                    }
                ),
                json.dumps(
                    {
                        "client_id": "client-b",
                        "path": "/v1/models",
                        "model": "",
                        "status_code": 200,
                        "latency_ms": 20,
                    }
                ),
                "not json",
            ]
        )
        + "\n"
    )

    summary = summarize_edge_usage(log_path)

    assert summary.requests_total == 3
    assert summary.invalid_lines == 1
    assert [client.client_id for client in summary.clients] == ["client-a", "client-b"]
    client_a = summary.clients[0]
    client_b = summary.clients[1]
    assert client_b.requests == 1
    assert client_a.requests == 2
    assert client_a.failures == 1
    assert client_a.models == {"memory": 1, "agent": 1}
    assert client_a.endpoints == {"msm3-omlx-live": 1}
    assert client_a.latency_ms_p50 == 10
    assert client_a.latency_ms_p95 == 30
