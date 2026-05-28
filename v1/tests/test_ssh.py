"""Tests for SSH helpers."""

from unittest.mock import MagicMock, patch

from thunder_forge.cluster.ssh import scp_content, ssh_run


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_uses_node_shell(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """When shell is provided, ssh_run uses it directly — no fallback hack."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello", shell="zsh")
    cmd_args = mock_run.call_args[0][0]
    remote_cmd = cmd_args[-1]
    assert remote_cmd.startswith("zsh -lc")
    assert "|| bash" not in remote_cmd


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_no_stderr_suppression(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """stderr is NOT suppressed with 2>/dev/null."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello", shell="bash")
    remote_cmd = mock_run.call_args[0][0][-1]
    assert "2>/dev/null" not in remote_cmd


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_default_shell_fallback(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """Without explicit shell, falls back to platform detection."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello")
    assert mock_run.called


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=True)
def test_ssh_run_local_uses_shell(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """Local commands use the specified shell."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "127.0.0.1", "echo hello", shell="bash")
    cmd_args = mock_run.call_args[0][0]
    assert cmd_args[0] == "bash"


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_scp_content_remote(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """scp_content works for remote targets."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    scp_content("admin", "192.168.1.101", "content", "/tmp/file")
    assert mock_run.called
