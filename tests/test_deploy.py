"""Tests for deploy logic: plist generation, orchestration."""

import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from thunder_forge.cluster.config import Assignment, Model, ModelSource, Node, ServerArgs, load_cluster_config
from thunder_forge.cluster.deploy import deploy_node, generate_plist


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_generate_plist_uses_resolved_fields() -> None:
    """Plist uses node.home_dir and node.homebrew_prefix, not hardcoded paths."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/.local/bin/mlx_lm.server" in xml_str
    assert "/opt/homebrew/bin" in xml_str
    assert "/Users/admin/logs/" in xml_str


def test_deploy_node_disables_vector_instead_of_installing(config_path: Path) -> None:
    """Node deploys remove old Vector agents and do not install Vector."""
    config = load_cluster_config(config_path)
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    def fake_ssh_run(user: str, ip: str, command: str, **kwargs):
        if "id -u" in command:
            return SimpleNamespace(returncode=0, stdout="501\n", stderr="")
        if command.startswith("launchctl list com.mlx-lm-"):
            return SimpleNamespace(returncode=0, stdout='"PID" = 123;\n', stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch("thunder_forge.cluster.deploy.install_node_tools"),
        patch("thunder_forge.cluster.deploy.ssh_run", side_effect=fake_ssh_run) as ssh_mock,
        patch("thunder_forge.cluster.deploy.scp_content", return_value=ok),
    ):
        errors = deploy_node("msm1", config)

    assert errors == []
    commands = [call.args[2] for call in ssh_mock.call_args_list]
    assert not any("brew install" in command and "vector" in command for command in commands)
    assert any("launchctl bootout gui/501/com.vector" in command for command in commands)
    assert any("rm -f ~/Library/LaunchAgents/com.vector.plist" in command for command in commands)



def test_deploy_node_replaces_existing_port_before_bootstrap(config_path: Path) -> None:
    """Deploy must kill stale port holders before bootstrap to avoid false healthy services."""
    config = load_cluster_config(config_path)
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    def fake_ssh_run(user: str, ip: str, command: str, **kwargs):
        if "id -u" in command:
            return SimpleNamespace(returncode=0, stdout="501\n", stderr="")
        if command.startswith("launchctl list com.mlx-lm-"):
            return SimpleNamespace(returncode=0, stdout='"PID" = 123;\n', stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch("thunder_forge.cluster.deploy.install_node_tools"),
        patch("thunder_forge.cluster.deploy.disable_vector_agent"),
        patch("thunder_forge.cluster.deploy.ssh_run", side_effect=fake_ssh_run) as ssh_mock,
        patch("thunder_forge.cluster.deploy.scp_content", return_value=ok),
    ):
        errors = deploy_node("msm1", config)

    assert errors == []
    commands = [call.args[2] for call in ssh_mock.call_args_list]
    bootout_index = next(
        i for i, command in enumerate(commands) if "launchctl bootout gui/501/com.mlx-lm-8000" in command
    )
    kill_index = next(i for i, command in enumerate(commands) if "lsof -ti :8000" in command)
    bootstrap_index = next(i for i, command in enumerate(commands) if "launchctl bootstrap gui/501" in command)
    assert bootout_index < kill_index < bootstrap_index
    assert "rm -f ~/Library/LaunchAgents/com.mlx-lm-8000.plist" in commands[bootout_index]
    assert not any("launchctl kickstart" in command for command in commands)


def test_compose_does_not_define_victorialogs() -> None:
    compose_path = Path(__file__).parent.parent / "docker" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    assert "victorialogs" not in compose["services"]
    assert "victorialogs-data" not in compose.get("volumes", {})
    admin_env = compose["services"]["admin-ui"]["environment"]
    assert "VICTORIALOGS_URL" not in admin_env


def test_generate_plist_non_default_homebrew() -> None:
    """Plist uses custom homebrew prefix (e.g. Intel Mac)."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/usr/local",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/usr/local/bin" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_no_homebrew() -> None:
    """Plist works without homebrew (Linux node)."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/home/admin",
        homebrew_prefix=None,
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/home/admin/.local/bin/mlx_lm.server" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_requires_resolved_fields() -> None:
    """Plist raises error if resolved fields are missing."""
    node = Node(host="msm1-wifi.lan", ram_gb=128, user="admin", role="node")
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    with pytest.raises(ValueError, match="pre-flight"):
        generate_plist(model, slot, node)


def test_generate_plist_basic(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    # Simulate pre-flight populating resolved fields
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    xml_str = generate_plist(model, slot, node)

    root = ET.fromstring(xml_str)
    assert root.tag == "plist"

    assert "com.mlx-lm-8000" in xml_str
    assert "mlx-community/Qwen3-Coder-Next-4bit" in xml_str
    assert "--model" in xml_str
    assert "--port" in xml_str
    assert "8000" in xml_str
    assert "--host" in xml_str
    assert "0.0.0.0" in xml_str
    assert "--continuous-batching" not in xml_str
    assert "--max-model-len" not in xml_str
    assert "HF_HUB_OFFLINE" in xml_str
    assert "Interactive" in xml_str


def test_generate_plist_with_extra_args(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    model.extra_args = ["--trust-remote-code", "--log-level", "DEBUG"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--trust-remote-code" in xml_str
    assert "DEBUG" in xml_str


def test_generate_plist_enable_thinking_false(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    model.enable_thinking = False
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--chat-template-args" in xml_str
    assert '"enable_thinking": false' in xml_str


def test_generate_plist_enable_thinking_none(config_path: Path) -> None:
    """enable_thinking=None (unset) should not add --chat-template-args."""
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--chat-template-args" not in xml_str


def _resolved_node() -> Node:
    return Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )


def test_generate_plist_server_args_all_fields() -> None:
    """All ServerArgs fields are emitted as CLI flags in ProgramArguments."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(
            decode_concurrency=48,
            prompt_concurrency=16,
            prefill_step_size=1024,
            prompt_cache_size=100,
            prompt_cache_bytes=1073741824,
            max_tokens=8192,
            temp=0.7,
            top_p=0.9,
            top_k=50,
            min_p=0.1,
            draft_model="mlx-community/Qwen3-0.6B-4bit",
            num_draft_tokens=5,
        ),
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert ">48<" in xml_str
    assert "--prompt-concurrency" in xml_str
    assert ">16<" in xml_str
    assert "--prefill-step-size" in xml_str
    assert ">1024<" in xml_str
    assert "--prompt-cache-size" in xml_str
    assert ">100<" in xml_str
    assert "--prompt-cache-bytes" in xml_str
    assert ">1073741824<" in xml_str
    assert "--max-tokens" in xml_str
    assert ">8192<" in xml_str
    assert "--temp" in xml_str
    assert ">0.7<" in xml_str
    assert "--top-p" in xml_str
    assert ">0.9<" in xml_str
    assert "--top-k" in xml_str
    assert ">50<" in xml_str
    assert "--min-p" in xml_str
    assert ">0.1<" in xml_str
    assert "--draft-model" in xml_str
    assert "mlx-community/Qwen3-0.6B-4bit" in xml_str
    assert "--num-draft-tokens" in xml_str
    assert ">5<" in xml_str


def test_generate_plist_server_args_none() -> None:
    """server_args=None emits no extra flags."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=None,
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" not in xml_str
    assert "--prompt-concurrency" not in xml_str
    assert "--max-tokens" not in xml_str


def test_generate_plist_server_args_partial() -> None:
    """Only non-None ServerArgs fields are emitted."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(decode_concurrency=64),
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert ">64<" in xml_str
    assert "--prompt-concurrency" not in xml_str
    assert "--max-tokens" not in xml_str


def test_generate_plist_server_args_before_extra_args() -> None:
    """server_args flags appear before extra_args in ProgramArguments."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(decode_concurrency=48),
        extra_args=["--trust-remote-code"],
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert "--trust-remote-code" in xml_str
    # decode-concurrency must appear before trust-remote-code
    assert xml_str.index("--decode-concurrency") < xml_str.index("--trust-remote-code")


def test_generate_plist_log_paths() -> None:
    """Logs go to ~/logs/mlx-lm-{port}.log, not /tmp/."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/logs/mlx-lm-8000.log" in xml_str
    assert "/Users/admin/logs/mlx-lm-8000.err" in xml_str
    assert "vllm" not in xml_str


def test_generate_plist_embedding_uses_mlx_openai_server() -> None:
    """Embedding models use mlx-openai-server instead of mlx_lm.server."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"),
        disk_gb=0.5,
        serving="embedding",
    )
    slot = Assignment(model="embedding-fast", port=8002)
    xml_str = generate_plist(model, slot, node)
    root = ET.fromstring(xml_str)
    args = [s.text for s in root.findall(".//array/string")]
    assert args[0] == "/Users/admin/.local/bin/mlx-openai-server"
    assert "launch" in args
    assert "--model-type" in args
    assert args[args.index("--model-type") + 1] == "embeddings"
    assert "--model-path" in args
    assert args[args.index("--model-path") + 1] == "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"
    assert args[args.index("--port") + 1] == "8002"
    assert args[args.index("--host") + 1] == "0.0.0.0"
    assert "mlx_lm.server" not in xml_str


def test_generate_plist_embedding_with_extra_args() -> None:
    """Embedding models support extra_args."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"),
        disk_gb=0.5,
        serving="embedding",
        extra_args=["--log-level", "DEBUG"],
    )
    slot = Assignment(model="embedding-fast", port=8002)
    xml_str = generate_plist(model, slot, node)
    root = ET.fromstring(xml_str)
    args = [s.text for s in root.findall(".//array/string")]
    assert "--log-level" in args
    assert "DEBUG" in args



def test_generate_plist_mlx_openai_server_lm_uses_mlx_openai_server() -> None:
    """mlx-openai-server LM models use mlx-openai-server with --model-type lm."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
        disk_gb=12.1,
        serving="mlx-openai-server",
        extra_args=["--reasoning-parser", "harmony", "--tool-call-parser", "harmony"],
    )
    slot = Assignment(model="gpt-oss-20b", port=8006)
    xml_str = generate_plist(model, slot, node)
    root = ET.fromstring(xml_str)
    args = [s.text for s in root.findall(".//array/string")]
    assert args[0] == "/Users/admin/.local/bin/mlx-openai-server"
    assert args[1] == "launch"
    assert args[args.index("--model-type") + 1] == "lm"
    assert args[args.index("--model-path") + 1] == "mlx-community/gpt-oss-20b-MXFP4-Q8"
    assert args[args.index("--port") + 1] == "8006"
    assert args[args.index("--host") + 1] == "0.0.0.0"
    assert "--no-log-file" in args
    assert "--reasoning-parser" in args
    assert args[args.index("--reasoning-parser") + 1] == "harmony"
    assert "--tool-call-parser" in args
    assert args[args.index("--tool-call-parser") + 1] == "harmony"
    assert "mlx_lm.server" not in xml_str


def test_health_poll_requires_openapi_for_mlx_openai_server() -> None:
    """mlx-openai-server health must not accept stale mlx_lm.server /v1/models only."""
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        serving="mlx-openai-server",
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeOpener:
        def __init__(self):
            self.urls = []

        def open(self, url: str, timeout: int = 5):
            self.urls.append(url)
            if url.endswith("/openapi.json"):
                raise ConnectionError("404")
            return FakeResponse()

    opener = FakeOpener()
    from thunder_forge.cluster.deploy import health_poll

    with patch.object(urllib.request, "build_opener", return_value=opener):
        assert health_poll("msm4-wifi.lan", 8006, model=model, timeout_secs=1, interval=0) is False
    assert any(url.endswith("/openapi.json") for url in opener.urls)


def test_generate_plist_non_embedding_uses_mlx_lm() -> None:
    """Non-embedding models still use mlx_lm.server."""
    node = Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "mlx_lm.server" in xml_str
    assert "mlx-openai-server" not in xml_str
