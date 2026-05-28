"""Tests for admin deploy checks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

# --- check_config ---

def _valid_config() -> dict:
    return {
        "models": {
            "llama": {
                "source": {"type": "huggingface", "repo": "mlx-community/Llama-3.2-3B-Instruct-4bit"},
                "disk_gb": 2.0,
                "ram_gb": None,
            }
        },
        "nodes": {"msm1": {"ip": "10.0.0.1", "ram_gb": 64, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "llama", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }


def test_check_config_ok():
    from thunder_admin.checks import check_config

    status, detail = check_config(_valid_config())
    assert status == "ok"
    assert detail == ""


def test_check_config_error_missing_model():
    from thunder_admin.checks import check_config

    config = _valid_config()
    config["assignments"]["msm1"][0]["model"] = "nonexistent"
    status, detail = check_config(config)
    assert status == "error"
    assert "nonexistent" in detail


def test_check_config_error_message_capped_at_120_chars():
    from thunder_admin.checks import check_config

    config = _valid_config()
    # Create many errors: reference non-existent models on many ports
    config["assignments"]["msm1"] = [{"model": f"missing_{i}", "port": 8000 + i} for i in range(10)]
    status, detail = check_config(config)
    assert status == "error"
    assert len(detail) <= 120


# --- check_ssh ---


def test_check_ssh_ok():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"ok\n"
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("ok", "")
    assert conn is mock_client


def test_check_ssh_timeout():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = TimeoutError("timed out")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("error", "SSH timeout")
    assert conn is None


def test_check_ssh_unexpected_exception():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("host key mismatch")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result[0] == "error"
    assert "host key mismatch" in result[1]
    assert conn is None


# --- check_model ---


def _make_ssh_conn(stdout_output: bytes, exit_code: int = 0) -> MagicMock:
    """Return a mock SSHClient whose exec_command returns the given stdout."""
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_output
    mock_channel = MagicMock()
    mock_channel.recv_exit_status.return_value = exit_code
    mock_stdout.channel = mock_channel
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
    return mock_client


def test_check_model_hf_found():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"snapshots\nrefs\n", exit_code=0)

    result = check_model(conn, node, slot, cluster)
    assert result == ("ok", "")
    # Verify the correct path was checked
    call_args = conn.exec_command.call_args[0][0]
    assert "models--mlx-community--Llama-3.2-3B" in call_args


def test_check_model_hf_not_found():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"", exit_code=2)  # ls returns 2 = no such file

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "not found" in result[1]


def test_check_model_non_hf_source_returns_warn():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="local_m", port=8000)
    cluster = ClusterConfig(
        models={"local_m": Model(source=ModelSource(type="local", path="/models/llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()

    for source_type in ("local", "pip", "convert"):
        cluster.models["local_m"].source.type = source_type
        result = check_model(conn, node, slot, cluster)
        assert result == ("warn", "non-HF source; skipping model check")
    conn.exec_command.assert_not_called()


def test_check_model_ssh_exception():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()
    conn.exec_command.side_effect = Exception("channel closed")

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "channel closed" in result[1]


# --- check_service ---


def _make_ssh_for_service(uname_output: bytes, service_stdout: bytes, service_exit: int) -> MagicMock:
    """Return a mock SSH client that returns uname output, then service output."""
    mock_client = MagicMock()
    call_count = [0]

    def exec_command(cmd, timeout=None):
        call_count[0] += 1
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_stdout.channel = mock_channel
        if "uname" in cmd:
            mock_stdout.read.return_value = uname_output
            mock_channel.recv_exit_status.return_value = 0
        else:
            mock_stdout.read.return_value = service_stdout
            mock_channel.recv_exit_status.return_value = service_exit
        return MagicMock(), mock_stdout, MagicMock()

    mock_client.exec_command.side_effect = exec_command
    return mock_client


def test_check_service_macos_running():
    from thunder_admin.checks import check_service

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    launchctl_output = b'{\n\t"PID" = 12345;\n\t"Label" = "com.mlx-lm-8000";\n}\n'
    conn = _make_ssh_for_service(b"Darwin\n", launchctl_output, 0)

    result = check_service(conn, node, slot)
    assert result == ("ok", "")
    # Verify launchctl was used
    calls = [str(c) for c in conn.exec_command.call_args_list]
    assert any("launchctl" in c for c in calls)
    assert any("com.mlx-lm-8000" in c for c in calls)


def test_check_service_macos_not_running():
    from thunder_admin.checks import check_service

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    # launchctl output without PID = not running
    launchctl_output = b'{\n\t"Label" = "com.mlx-lm-8000";\n}\n'
    conn = _make_ssh_for_service(b"Darwin\n", launchctl_output, 0)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "com.mlx-lm-8000" in result[1]


def test_check_service_macos_not_found():
    from thunder_admin.checks import check_service

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    conn = _make_ssh_for_service(b"Darwin\n", b"Could not find service\n", 1)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "com.mlx-lm-8000" in result[1]


def test_check_service_linux_active():
    from thunder_admin.checks import check_service

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.2", ram_gb=64, user="ubuntu")
    slot = Assignment(model="llama", port=9000)
    conn = _make_ssh_for_service(b"Linux\n", b"active\n", 0)

    result = check_service(conn, node, slot)
    assert result == ("ok", "")
    calls = [str(c) for c in conn.exec_command.call_args_list]
    assert any("systemctl" in c for c in calls)
    assert any("thunder-forge-9000" in c for c in calls)


def test_check_service_linux_inactive():
    from thunder_admin.checks import check_service

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.2", ram_gb=64, user="ubuntu")
    slot = Assignment(model="llama", port=9000)
    conn = _make_ssh_for_service(b"Linux\n", b"inactive\n", 3)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "thunder-forge-9000" in result[1]


# --- check_port ---


def test_check_port_ok():
    from thunder_admin.checks import check_port

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("thunder_admin.checks.httpx.get", return_value=mock_response) as mock_get:
        result = check_port(node, slot)

    assert result == ("ok", "")
    mock_get.assert_called_once_with("http://10.0.0.1:8000/v1/models", timeout=3)


def test_check_port_non_200():
    from thunder_admin.checks import check_port

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("thunder_admin.checks.httpx.get", return_value=mock_response):
        result = check_port(node, slot)

    assert result[0] == "error"
    assert "503" in result[1]


def test_check_port_timeout():
    import httpx
    from thunder_admin.checks import check_port

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    with patch("thunder_admin.checks.httpx.get", side_effect=httpx.TimeoutException("timed out")):
        result = check_port(node, slot)

    assert result == ("error", "timeout")


def test_check_port_connection_error():
    import httpx
    from thunder_admin.checks import check_port

    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    with patch("thunder_admin.checks.httpx.get", side_effect=httpx.ConnectError("refused")):
        result = check_port(node, slot)

    assert result[0] == "error"
    assert "refused" in result[1]


# --- run_all_checks ---

def _full_config() -> dict:
    return {
        "models": {
            "llama": {
                "source": {"type": "huggingface", "repo": "mlx-community/Llama-3.2-3B"},
                "disk_gb": 2.0,
            }
        },
        "nodes": {"msm1": {"ip": "10.0.0.1", "ram_gb": 64, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "llama", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }


def test_run_all_checks_happy_path():
    from thunder_admin.checks import run_all_checks

    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), MagicMock())):
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                        results = run_all_checks(_full_config())

    assert ("msm1", 8000) in results
    slot = results[("msm1", 8000)]
    assert slot["config"] == ("ok", "")
    assert slot["ssh"] == ("ok", "")
    assert slot["model"] == ("ok", "")
    assert slot["service"] == ("ok", "")
    assert slot["port"] == ("ok", "")


def test_run_all_checks_ssh_fail_skips_downstream():
    from thunder_admin.checks import run_all_checks

    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("error", "SSH timeout"), None)):
            with patch("thunder_admin.checks.check_model") as mock_model:
                with patch("thunder_admin.checks.check_service") as mock_service:
                    with patch("thunder_admin.checks.check_port") as mock_port:
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["ssh"] == ("error", "SSH timeout")
    assert slot["model"] == ("skip", "")
    assert slot["service"] == ("skip", "")
    assert slot["port"] == ("skip", "")
    mock_model.assert_not_called()
    mock_service.assert_not_called()
    mock_port.assert_not_called()


def test_run_all_checks_service_not_ok_skips_port():
    from thunder_admin.checks import run_all_checks

    mock_ssh_conn = MagicMock()
    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)):
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("error", "not running")):
                    with patch("thunder_admin.checks.check_port") as mock_port:
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["service"] == ("error", "not running")
    assert slot["port"] == ("skip", "")
    mock_port.assert_not_called()


def test_run_all_checks_config_error_does_not_block_ssh():
    from thunder_admin.checks import run_all_checks

    mock_ssh_conn = MagicMock()
    with patch("thunder_admin.checks.check_config", return_value=("error", "RAM too low")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)) as mock_ssh:
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["config"] == ("error", "RAM too low")
    assert slot["ssh"] == ("ok", "")
    mock_ssh.assert_called_once()


def test_run_all_checks_no_user_returns_error():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["nodes"]["msm1"]["user"] = ""

    with patch.dict("os.environ", {}, clear=True):
        with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
            results = run_all_checks(config)

    slot = results[("msm1", 8000)]
    assert slot["ssh"][0] == "error"
    assert "user not configured" in slot["ssh"][1]
    assert slot["model"] == ("skip", "")
    assert slot["service"] == ("skip", "")
    assert slot["port"] == ("skip", "")


def test_run_all_checks_user_fallback_to_env():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["nodes"]["msm1"]["user"] = ""

    mock_ssh_conn = MagicMock()
    with patch.dict("os.environ", {"GATEWAY_SSH_USER": "fallback_user"}):
        with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
            with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)) as mock_ssh:
                with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                        with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                            results = run_all_checks(config)

    called_node = mock_ssh.call_args[0][0]
    assert called_node.user == "fallback_user"
    assert results[("msm1", 8000)]["ssh"] == ("ok", "")


def test_run_all_checks_empty_assignments_returns_empty():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["assignments"] = {}
    results = run_all_checks(config)
    assert results == {}
