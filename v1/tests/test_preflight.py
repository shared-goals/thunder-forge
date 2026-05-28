"""Tests for pre-flight validation."""

from unittest.mock import MagicMock, patch

from thunder_forge.cluster.config import ClusterConfig, Node
from thunder_forge.cluster.preflight import build_probe_script, parse_probe_output, run_preflight


def _make_config(nodes: dict[str, Node], assignments: dict[str, list] | None = None) -> ClusterConfig:
    return ClusterConfig(models={}, nodes=nodes, assignments=assignments or {})


class TestParseProbeOutput:
    def test_parses_valid_output(self) -> None:
        output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "MLX_LM_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        result = parse_probe_output(output)
        assert result["PLATFORM"] == "Darwin"
        assert result["SHELL_PATH"] == "zsh"
        assert result["HOME_DIR"] == "/Users/admin"
        assert result["BREW_PREFIX"] == "/opt/homebrew"

    def test_returns_empty_on_missing_delimiters(self) -> None:
        result = parse_probe_output("some random output")
        assert result == {}


class TestBuildProbeScript:
    def test_contains_platform_and_shell_probes(self) -> None:
        script = build_probe_script(role="node")
        assert "uname -s" in script
        assert "SHELL" in script
        assert "HOME" in script
        assert "brew --prefix" in script
        assert "@@PROBE_START@@" in script
        assert "@@PROBE_END@@" in script

    def test_gateway_includes_docker_check(self) -> None:
        script = build_probe_script(role="gateway")
        assert "docker" in script

    def test_node_includes_mlx_lm_check(self) -> None:
        script = build_probe_script(role="node")
        assert "mlx-lm" in script


class TestRunPreflight:
    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_all_nodes_ok(self, mock_run: MagicMock) -> None:
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "MLX_LM_OK=1\n"
            "DOCKER_OK=1\n"
            "HF_HOME_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=probe_output, stderr="")
        nodes = {
            "msm1": Node(ip="192.168.1.101", ram_gb=128, role="node"),
            "rock": Node(ip="192.168.1.61", ram_gb=32, role="gateway"),
        }
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert errors == []
        assert config.nodes["msm1"].platform == "Darwin"
        assert config.nodes["msm1"].shell == "zsh"
        assert config.nodes["msm1"].home_dir == "/Users/admin"

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_ssh_unreachable(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = TimeoutError("Connection timed out")
        nodes = {"msm1": Node(ip="192.168.1.101", ram_gb=128, role="node")}
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert len(errors) == 1
        assert "Cannot reach msm1" in errors[0]

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_partial_failure_continues(self, mock_run: MagicMock) -> None:
        """If one node fails, others still get checked."""
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "MLX_LM_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "192.168.1.101" in str(cmd):
                raise TimeoutError("timeout")
            return MagicMock(returncode=0, stdout=probe_output, stderr="")

        mock_run.side_effect = side_effect
        nodes = {
            "msm1": Node(ip="192.168.1.101", ram_gb=128, role="node"),
            "msm2": Node(ip="192.168.1.102", ram_gb=128, role="node"),
        }
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert len(errors) == 1
        assert "msm1" in errors[0]
        # msm2 should have been populated
        assert config.nodes["msm2"].platform == "Darwin"

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_missing_uv_reported(self, mock_run: MagicMock) -> None:
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=0\n"
            "MLX_LM_OK=0\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=probe_output, stderr="")
        nodes = {"msm1": Node(ip="192.168.1.101", ram_gb=128, role="node")}
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert any("uv not found" in e for e in errors)
