"""Tests for the minimal Thunder Forge edge contract."""

import json

import httpx
import pytest

from thunder_forge.cluster.edge import (
    EdgeAccessLog,
    EdgeClient,
    EdgeModelCatalogEntry,
    EdgeProxyConfig,
    authenticate_edge_request,
    build_edge_access_log,
    build_edge_clients_from_env,
    edge_api_key_from_env,
    edge_models_payload,
    ensure_edge_api_keys,
    ensure_olla_session_id,
    load_edge_clients_from_env,
    proxy_edge_request,
    rewrite_openai_path,
    smoke_edge_contract,
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


def test_edge_auth_rejects_non_canonical_bearer_header_spacing() -> None:
    clients = {"dev-secret": EdgeClient(client_id="client-a")}

    result = authenticate_edge_request("Bearer  dev-secret", clients)

    assert result.allowed is False
    assert result.status_code == 401


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


def test_edge_client_loader_rejects_duplicate_api_keys() -> None:
    env = {
        "TF_USER_CLIENT_A": "shared-secret",
        "TF_USER_CLIENT_B": "shared-secret",
    }

    with pytest.raises(ValueError, match="duplicate API key"):
        load_edge_clients_from_env(env=env)


def test_edge_client_env_names_encode_dash_and_dot_without_collisions() -> None:
    env = {
        "TF_USER_CLIENT_A": "underscore-secret",
        "TF_USER_CLIENT_DASH_A": "dash-secret",
        "TF_USER_CLIENT_DOT_A": "dot-secret",
        "TF_USER_CLIENT_UNDERSCORE_DASH_A": "literal-dash-word-secret",
        "TF_USER_CLIENT_UNDERSCORE_DOT_A": "literal-dot-word-secret",
        "TF_USER_CLIENT_UNDERSCORE_UNDERSCORE_A": "literal-underscore-word-secret",
    }

    assert edge_api_key_from_env(env=env, client_id="client_a") == ("TF_USER_CLIENT_A", "underscore-secret")
    assert edge_api_key_from_env(env=env, client_id="client-a") == ("TF_USER_CLIENT_DASH_A", "dash-secret")
    assert edge_api_key_from_env(env=env, client_id="client.a") == ("TF_USER_CLIENT_DOT_A", "dot-secret")
    assert edge_api_key_from_env(env=env, client_id="client_dash_a") == (
        "TF_USER_CLIENT_UNDERSCORE_DASH_A",
        "literal-dash-word-secret",
    )
    assert edge_api_key_from_env(env=env, client_id="client_dot_a") == (
        "TF_USER_CLIENT_UNDERSCORE_DOT_A",
        "literal-dot-word-secret",
    )
    assert edge_api_key_from_env(env=env, client_id="client_underscore_a") == (
        "TF_USER_CLIENT_UNDERSCORE_UNDERSCORE_A",
        "literal-underscore-word-secret",
    )

    clients = load_edge_clients_from_env(env=env)

    assert clients["underscore-secret"].client_id == "client_a"
    assert clients["dash-secret"].client_id == "client-a"
    assert clients["dot-secret"].client_id == "client.a"
    assert clients["literal-dash-word-secret"].client_id == "client_dash_a"
    assert clients["literal-dot-word-secret"].client_id == "client_dot_a"
    assert clients["literal-underscore-word-secret"].client_id == "client_underscore_a"


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


def test_edge_models_payload_uses_tf_aliases_with_base_id_descriptions() -> None:
    payload = edge_models_payload(
        [
            EdgeModelCatalogEntry(
                id="coder",
                name="coder",
                description="mlx-community/Qwen3-Coder-Next-4bit; runtime: Qwen3-Coder-Next-4bit",
                runtime_model_id="Qwen3-Coder-Next-4bit",
                source_repo="mlx-community/Qwen3-Coder-Next-4bit",
                context_length=262144,
            ),
            EdgeModelCatalogEntry(
                id="agent-better",
                name="agent-better",
                description="mlx-community/Qwen3.6-35B-A3B-mxfp8; runtime: Qwen3.6-35B-A3B-mxfp8",
                runtime_model_id="Qwen3.6-35B-A3B-mxfp8",
                source_repo="mlx-community/Qwen3.6-35B-A3B-mxfp8",
            ),
        ]
    )

    assert payload["object"] == "list"
    data = payload["data"]
    assert isinstance(data, list)
    assert [item["id"] for item in data] == ["agent-better", "coder"]
    assert data[0]["name"] == "agent-better"
    assert "mlx-community/Qwen3.6-35B-A3B-mxfp8" in str(data[0]["description"])
    assert data[0]["tf_runtime_model_id"] == "Qwen3.6-35B-A3B-mxfp8"
    assert data[1]["context_length"] == 262144


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
        model="qwen3-1.7b-omlx-infer-03-test",
        status_code=200,
        latency_ms=42,
        olla_endpoint="infer-03-omlx-live",
        api_key="dev-secret",
    )

    assert isinstance(record, EdgeAccessLog)
    payload = record.to_json_dict()
    assert "timestamp" in payload
    assert isinstance(payload["timestamp"], str)
    assert payload["request_id"] == "req-1"
    assert payload["client_id"] == "client-a"
    assert payload["model"] == "qwen3-1.7b-omlx-infer-03-test"
    assert payload["status_code"] == 200
    assert payload["latency_ms"] == 42
    assert payload["olla_endpoint"] == "infer-03-omlx-live"
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


def test_proxy_edge_request_logs_auth_failures_without_calling_olla() -> None:
    requests: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"unexpected": True})

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
    )

    result = proxy_edge_request(
        method="GET",
        path="/v1/models",
        headers={"Authorization": "Bearer wrong-secret"},
        body=b"",
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 401
    assert requests == []
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "unauthenticated"
    assert logged["path"] == "/v1/models"
    assert logged["status_code"] == 401
    assert "wrong-secret" not in logs[0]


def test_proxy_edge_request_rejects_oversized_body_without_calling_olla() -> None:
    requests: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"unexpected": True})

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
        max_body_bytes=4,
    )

    result = proxy_edge_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"Authorization": "Bearer dev-secret", "Content-Type": "application/json"},
        body=b"12345",
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 413
    assert requests == []
    assert result.body == b'{"error":"request_too_large"}'
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["status_code"] == 413


def test_proxy_edge_request_serves_tf_alias_model_catalog_without_calling_olla() -> None:
    requests: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"unexpected": True})

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
        model_catalog=[
            EdgeModelCatalogEntry(
                id="coder",
                name="coder",
                description="mlx-community/Qwen3-Coder-Next-4bit",
                runtime_model_id="Qwen3-Coder-Next-4bit",
                source_repo="mlx-community/Qwen3-Coder-Next-4bit",
            )
        ],
    )

    result = proxy_edge_request(
        method="GET",
        path="/v1/models",
        headers={"Authorization": "Bearer dev-secret"},
        body=b"",
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    assert requests == []
    payload = json.loads(result.body)
    assert [item["id"] for item in payload["data"]] == ["coder"]
    assert payload["data"][0]["description"] == "mlx-community/Qwen3-Coder-Next-4bit"
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["path"] == "/v1/models"
    assert logged["model"] == ""
    assert logged["status_code"] == 200


def test_proxy_edge_request_forwards_streaming_chat_without_logging_secrets() -> None:
    forwarded: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream", "X-Olla-Endpoint": "infer-03-omlx-live"},
            content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
        )

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
                "model": "qwen3-1.7b-omlx-infer-03-test",
                "messages": [{"role": "user", "content": "do not log me"}],
                "stream": True,
            }
        ).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    assert result.headers["Content-Type"] == "text/event-stream"
    assert result.headers["X-Olla-Endpoint"] == "infer-03-omlx-live"
    assert b"data:" in result.body
    assert len(forwarded) == 1
    assert forwarded[0].url == "http://olla.local:40115/olla/openai-compatible/v1/chat/completions"
    assert json.loads(forwarded[0].content)["stream"] is True
    assert all("dev-secret" not in entry for entry in logs)
    assert all("do not log me" not in entry for entry in logs)
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["model"] == "qwen3-1.7b-omlx-infer-03-test"
    assert logged["status_code"] == 200


def test_proxy_edge_request_rewrites_path_forwards_session_and_logs_without_secret() -> None:
    forwarded: list[httpx.Request] = []
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(
            200,
            headers={"X-Olla-Endpoint": "infer-03-omlx-live"},
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
        body=json.dumps({"model": "qwen3-1.7b-omlx-infer-03-test", "messages": []}).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    assert result.body == b'{"id":"chatcmpl-1","choices":[{"message":{"content":"pong"}}]}'
    assert result.headers["X-Olla-Endpoint"] == "infer-03-omlx-live"
    assert len(forwarded) == 1
    assert forwarded[0].method == "POST"
    assert forwarded[0].url == "http://olla.local:40115/olla/openai-compatible/v1/chat/completions"
    assert forwarded[0].headers["X-Olla-Session-ID"] == "session-123"
    assert "authorization" not in forwarded[0].headers
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["path"] == "/v1/chat/completions"
    assert logged["model"] == "qwen3-1.7b-omlx-infer-03-test"
    assert logged["status_code"] == 200
    assert logged["olla_endpoint"] == "infer-03-omlx-live"
    assert "dev-secret" not in logs[0]


def test_proxy_edge_request_records_upstream_usage_tokens() -> None:
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Olla-Endpoint": "infer-03-omlx-live"},
            json={
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": "pong"}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            },
        )

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
    )

    result = proxy_edge_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"Authorization": "Bearer dev-secret", "Content-Type": "application/json"},
        body=json.dumps({"model": "memory", "messages": []}).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    logged = json.loads(logs[0])
    assert logged["prompt_tokens"] == 12
    assert logged["completion_tokens"] == 8
    assert logged["total_tokens"] == 20


def test_proxy_edge_request_logs_upstream_failures_without_secret() -> None:
    logs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = EdgeProxyConfig(
        olla_base_url="http://olla.local:40115",
        clients_by_key={"dev-secret": EdgeClient(client_id="client-a")},
        access_log_sink=logs.append,
    )

    result = proxy_edge_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"Authorization": "Bearer dev-secret", "Content-Type": "application/json"},
        body=json.dumps({"model": "memory", "messages": []}).encode(),
        config=config,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 502
    assert len(logs) == 1
    logged = json.loads(logs[0])
    assert logged["client_id"] == "client-a"
    assert logged["model"] == "memory"
    assert logged["status_code"] == 502
    assert "dev-secret" not in logs[0]


def test_smoke_edge_contract_fails_when_same_session_routes_to_different_endpoints() -> None:
    chat_endpoints = ["infer-01-omlx-live", "infer-02-omlx-live"]
    chat_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_count
        if request.method == "GET" and request.url.path == "/v1/models":
            if request.headers.get("Authorization") == "Bearer dev-secret":
                return httpx.Response(200, json={"object": "list", "data": [{"id": "memory"}]})
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            assert request.headers.get("X-Olla-Session-ID") == "tf-smoke-session"
            endpoint = chat_endpoints[min(chat_count, len(chat_endpoints) - 1)]
            chat_count += 1
            return httpx.Response(
                200,
                headers={"X-Olla-Endpoint": endpoint},
                json={"id": "chatcmpl-1", "choices": [{"message": {"content": "pong"}}]},
            )
        return httpx.Response(500, text=f"unexpected path: {request.url.path}")

    result = smoke_edge_contract(
        base_url="http://edge.local:40116",
        api_key="dev-secret",
        model="memory",
        transport=httpx.MockTransport(handler),
    )

    assert result.chat_ok is True
    assert result.session_ok is False
    assert chat_count == 2
    assert "same session routed to different endpoints" in result.errors


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
                        "olla_endpoint": "infer-03-omlx-live",
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
    assert client_a.endpoints == {"infer-03-omlx-live": 1}
    assert client_a.latency_ms_p50 == 10
    assert client_a.latency_ms_p95 == 30
