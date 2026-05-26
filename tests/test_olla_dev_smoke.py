"""Tests for Olla dev-smoke orchestration helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from thunder_forge.cluster.olla import (
    OllaDevSmokeResult,
    OllaSmokeResult,
    dev_smoke_olla,
)


def test_dev_smoke_olla_generates_config_spawns_and_smokes(tmp_path: Path, monkeypatch) -> None:
    """dev_smoke_olla: generate config → spawn Olla → wait healthy → smoke → kill → report."""
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        "models: {}\n"
        "nodes:\n"
        "  studio:\n"
        "    host: studio.lan\n"
        "    ram_gb: 64\n"
        "    user: shag\n"
        "    role: gateway\n"
        "  msm3:\n"
        "    host: msm3-wifi.lan\n"
        "    ram_gb: 128\n"
        "    user: shag\n"
        "    role: node\n"
        "    runtime:\n"
        "      type: omlx\n"
        "      port: 8018\n"
        "runtime_routes:\n"
        "  - model_name: qwen3-1.7b-omlx-msm3-test\n"
        "    runtime: omlx\n"
        "    node: msm3\n"
        "    model: Qwen3-1.7B-4bit\n"
        "assignments: {}\n"
    )

    import thunder_forge.cluster.config as config_module
    import thunder_forge.cluster.olla as olla_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    # Capture subprocess.Popen call
    popen_calls: list[list[str]] = []
    fake_proc = MagicMock()
    fake_proc.pid = 99999
    fake_proc.poll.return_value = None  # still running
    fake_proc.terminate.return_value = None
    fake_proc.wait.return_value = 0

    def mock_popen(cmd: list[str], **kwargs) -> MagicMock:
        popen_calls.append(cmd)
        return fake_proc

    monkeypatch.setattr(olla_module.subprocess, "Popen", mock_popen)

    # Mock httpx.Client to simulate a healthy Olla
    def mock_client(*, base_url: str, timeout: float, transport=None, trust_env: bool = False) -> MagicMock:
        client_ctx = MagicMock()
        client = MagicMock()

        def get_side_effect(path: str):
            resp = MagicMock()
            if path == "/internal/health":
                resp.status_code = 200
                resp.is_success = True
                resp.text = '{"status": "healthy"}'
            else:
                resp.status_code = 404
                resp.is_success = False
                resp.text = "not found"
            return resp

        client.get.side_effect = get_side_effect
        client_ctx.__enter__ = MagicMock(return_value=client)
        client_ctx.__exit__ = MagicMock(return_value=False)
        return client_ctx

    monkeypatch.setattr(olla_module.httpx, "Client", mock_client)

    # Mock smoke_olla_router to return a green result
    fake_smoke = OllaSmokeResult(
        base_url="http://127.0.0.1:40115",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-msm3-test",
        health_ok=True,
        endpoints_ok=True,
        models_ok=True,
        chat_ok=True,
        alias_ok=True,
        session_ok=True,
        root_v1_absent=True,
        latency_ms=245,
        olla_endpoint="msm3-omlx-live",
    )
    monkeypatch.setattr(olla_module, "smoke_olla_router", lambda **kw: fake_smoke)

    result = dev_smoke_olla(
        binary="/tmp/tf-gateway-close-look/olla-bin/olla",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-msm3-test",
    )

    # Config generated
    assert isinstance(result, OllaDevSmokeResult)
    assert result.config_generated is True
    assert (config_dir / "olla-config.yaml").exists()

    # Olla was spawned with the generated config
    assert len(popen_calls) == 1
    cmd = popen_calls[0]
    assert cmd[0] == "/tmp/tf-gateway-close-look/olla-bin/olla"
    assert "-config" in cmd
    config_idx = cmd.index("-config")
    assert cmd[config_idx + 1] == str(config_dir / "olla-config.yaml")

    # Olla was terminated
    fake_proc.terminate.assert_called_once()

    # Smoke passed through
    assert result.smoke_result is fake_smoke
    assert result.olla_terminated is True
    assert result.ok is True


def test_dev_smoke_olla_reports_failure_when_config_generation_fails(tmp_path: Path, monkeypatch) -> None:
    """dev_smoke_olla: should report failure if config generation fails."""
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: tmp_path)

    import thunder_forge.cluster.olla as olla_module

    monkeypatch.setattr(olla_module.subprocess, "Popen", MagicMock())

    result = dev_smoke_olla(
        binary="/tmp/tf-gateway-close-look/olla-bin/olla",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-msm3-test",
    )

    assert result.config_generated is False
    assert result.ok is False
    assert len(result.errors) > 0
