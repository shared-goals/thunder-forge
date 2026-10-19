"""Exercise the actual streaming HTTP handler over a local socket pair."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from thunder_forge.cluster import edge


@pytest.fixture
def exchange(monkeypatch):
    client_type = httpx.Client

    def run(config, upstream, *, timeout_scale=1):
        handlers = []

        def server(_address, handler):
            handlers.append(handler)
            return SimpleNamespace(serve_forever=lambda: None)

        monkeypatch.setattr(edge, "ThreadingHTTPServer", server)
        def proxy_client(**kwargs):
            kwargs["timeout"] *= timeout_scale
            return client_type(**kwargs, transport=httpx.MockTransport(upstream) if upstream else None)

        monkeypatch.setattr(edge.httpx, "Client", proxy_client)
        edge.serve_edge_proxy(host="127.0.0.1", port=0, config=config)
        body = json.dumps({"model": "agent", "messages": [], "stream": True}).encode()
        request = (
            b"POST /v1/chat/completions HTTP/1.0\r\n"
            b"Authorization: Bearer fixture-key\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        )
        errors = []
        client, connection = socket.socketpair()

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
        return wire

    return run


def test_edge_allows_a_response_within_olla_timeout(exchange):
    # Scale 90 seconds of header latency to 450 ms: Olla allows 120 seconds.
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(0.45)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: [DONE]\n\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as upstream:
        thread = threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        config = edge.EdgeProxyConfig(
            olla_base_url=f"http://127.0.0.1:{upstream.server_port}",
            clients_by_key={"fixture-key": edge.EdgeClient("fixture")},
        )
        try:
            wire = exchange(config, None, timeout_scale=0.005)
        finally:
            upstream.shutdown()
            thread.join(timeout=5)
    assert wire.startswith(b"HTTP/1.0 200")
    assert wire.endswith(b"data: [DONE]\n\n")


@pytest.mark.parametrize("failure", ["connect", "before_body", "after_chunk", None])
def test_stream_failure_sends_only_one_http_response(exchange, failure):
    chunk = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    closed = []
    records = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if failure == "before_body":
                raise httpx.ReadError("fixture read failure")
            yield chunk
            if failure == "after_chunk":
                raise httpx.ReadError("fixture read failure")
            yield b"data: [DONE]\n\n"

        def close(self):
            closed.append(True)

    def upstream(request):
        if failure == "connect":
            raise httpx.ConnectError("fixture connection failure", request=request)
        return httpx.Response(200, headers={
            "Content-Type": "text/event-stream", "X-Olla-Endpoint": "infer-01",
        }, stream=Stream())

    config = edge.EdgeProxyConfig(
        olla_base_url="http://olla.test", clients_by_key={"fixture-key": edge.EdgeClient("fixture")},
        access_log_sink=records.append,
    )
    wire = exchange(config, upstream)
    assert wire.count(b"HTTP/1.0 ") == 1
    assert wire.startswith(b"HTTP/1.0 502" if failure == "connect" else b"HTTP/1.0 200")
    body = wire.split(b"\r\n\r\n", 1)[1]
    if failure == "connect":
        assert json.loads(body)["error"].startswith("upstream_failed:")
    else:
        assert closed == [True]
        assert body == (b"" if failure == "before_body" else chunk if failure else chunk + b"data: [DONE]\n\n")
    assert config.active_requests == {}
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["status_code"] == (502 if failure else 200)
    if failure != "connect":
        assert record["olla_endpoint"] == "infer-01"


@pytest.mark.parametrize("inspect", [False, True])
def test_stream_capture_is_optional_and_bounded(exchange, monkeypatch, tmp_path, inspect):
    chunk = b"data: " + b"x" * 65536 + b"\n\n"
    chunks = 20
    retained = []

    class Capture(bytearray):
        def extend(self, data):
            super().extend(data)
            retained.append(len(self))

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(chunks):
                yield chunk

    monkeypatch.setattr(edge, "bytearray", Capture, raising=False)
    config = edge.EdgeProxyConfig(
        olla_base_url="http://olla.test", clients_by_key={"fixture-key": edge.EdgeClient("fixture")},
        inspector=edge.EdgeInspectorConfig(enabled=inspect, output_dir=tmp_path),
    )
    wire = exchange(config, lambda _request: httpx.Response(200, stream=Stream()))
    assert wire.split(b"\r\n\r\n", 1)[1] == chunk * chunks
    assert max(retained, default=0) <= (1024 * 1024 if inspect else 0)
    if inspect:
        entries = [
            json.loads(line)
            for path in tmp_path.glob("*/requests.jsonl")
            for line in path.read_text().splitlines()
        ]
        response = next(entry for entry in entries if entry["type"] == "response")
        assert len(response["body"].encode()) == 1024 * 1024
        assert response["body_truncated"] is True
    else:
        assert not list(tmp_path.glob("*/requests.jsonl"))
