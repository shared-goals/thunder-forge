from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from api.webhook import app
from services.config_service import load_config


def _make_init_data(*, bot_token: str, user_id: int) -> str:
    user = {"id": user_id, "username": "admin"}
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "AAEAAAEAAA==",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = dict(data)
    payload["hash"] = sig
    return urlencode(payload)


@pytest.fixture()
def client():
    return TestClient(app)


def test_status_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: TestClient):
    cfg = tmp_path / "tf.yml"
    cfg.write_text(
        """
telegram:
  bot_token: test-token

access:
  admin_telegram_ids:
    - 123

settings:
  ssh:
    connect_timeout_seconds: 0.05
    batch_mode: true
  monitor:
    ssh_port: 22
    ollama_port: 11434
  hosts_sync:
    managed_block_start: '# BEGIN thunder-forge'
    managed_block_end: '# END thunder-forge'

nodes:
  - name: node1
    ssh_user: u
    wifi_ip: 127.0.0.1
    tb_ip: 127.0.0.1
    service_manager: brew
    ollama_service: ollama
    models: []
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("TF_CONFIG_PATH", str(cfg))
    load_config.cache_clear()

    init_data = _make_init_data(bot_token="test-token", user_id=123)
    res = client.post("/api/mini-app/status", headers={"Authorization": "tma " + init_data})

    assert res.status_code == 200
    body = res.json()
    assert "ts" in body
    assert isinstance(body["nodes"], list)
    assert body["nodes"][0]["name"] == "node1"
    assert "wifi" in body["nodes"][0]
    assert "thunderbolt" in body["nodes"][0]
