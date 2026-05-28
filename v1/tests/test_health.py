"""Tests for health check logic."""

import json
from unittest.mock import MagicMock, patch

from thunder_forge.cluster.health import check_gateway_services, check_node


@patch("thunder_forge.cluster.health.urllib.request.build_opener")
def test_check_node_healthy(mock_build_opener: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response
    mock_build_opener.return_value = mock_opener

    result = check_node("192.168.1.101", 8000)
    assert result is True


@patch("thunder_forge.cluster.health.urllib.request.build_opener")
def test_check_node_unreachable(mock_build_opener: MagicMock) -> None:
    mock_opener = MagicMock()
    mock_opener.open.side_effect = ConnectionError("Connection refused")
    mock_build_opener.return_value = mock_opener

    result = check_node("192.168.1.101", 8000)
    assert result is False


@patch("thunder_forge.cluster.health.ssh_run")
def test_check_gateway_services_parses_json(mock_ssh: MagicMock) -> None:
    """Docker compose JSON output is parsed correctly."""
    services_json = "\n".join(
        [
            json.dumps({"Name": "docker-litellm-1", "State": "running", "Health": "healthy"}),
            json.dumps({"Name": "docker-openwebui-1", "State": "running", "Health": ""}),
            json.dumps({"Name": "docker-postgres-1", "State": "running", "Health": "healthy"}),
        ]
    )
    mock_ssh.return_value = MagicMock(returncode=0, stdout=services_json, stderr="")
    results = check_gateway_services("192.168.1.61", "infra_user")
    assert results["litellm"] is True
    assert results["openwebui"] is True
    assert results["postgres"] is True


@patch("thunder_forge.cluster.health.ssh_run")
def test_check_gateway_services_ssh_failure(mock_ssh: MagicMock) -> None:
    """SSH failure returns all services as unhealthy with error context."""
    mock_ssh.return_value = MagicMock(returncode=1, stdout="", stderr="Connection refused")
    results = check_gateway_services("192.168.1.61", "infra_user")
    assert all(v is False for v in results.values())
