"""Config parsing, memory validation, and Olla config generation."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ServingMode(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    CLI = "cli"
    MLX_OPENAI_SERVER = "mlx-openai-server"


class RuntimeType(StrEnum):
    OMLX = "omlx"


class NodeRole(StrEnum):
    NODE = "node"
    GATEWAY = "gateway"


def _represent_float(dumper: yaml.Dumper, value: float) -> yaml.ScalarNode:
    """Force decimal notation for small floats instead of scientific notation."""
    if value != value:  # NaN
        return dumper.represent_scalar("tag:yaml.org,2002:float", ".nan")
    if value == float("inf"):
        return dumper.represent_scalar("tag:yaml.org,2002:float", ".inf")
    if value == float("-inf"):
        return dumper.represent_scalar("tag:yaml.org,2002:float", "-.inf")
    text = f"{value:.10f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return dumper.represent_scalar("tag:yaml.org,2002:float", text)


yaml.add_representer(float, _represent_float)


@dataclass
class ModelSource:
    type: str  # huggingface, convert, local, pip
    repo: str = ""
    revision: str = "main"
    quantize: str = ""
    path: str = ""
    package: str = ""
    weight_repo: str = ""


@dataclass
class ModelInfo:
    base_model: str = ""                                # maps custom names to known models for token counting
    mode: str = ""                                      # chat, completion, embedding, image_generation
    input_cost_per_token: float | None = None           # cost tracking / budget enforcement
    output_cost_per_token: float | None = None          # cost tracking / budget enforcement
    supports_vision: bool | None = None                 # multimodal routing
    supports_function_calling: bool | None = None       # tool use routing
    supports_parallel_function_calling: bool | None = None  # parallel tool calls
    supports_response_schema: bool | None = None        # structured output support


@dataclass
class ServerArgs:
    decode_concurrency: int | None = None    # --decode-concurrency (mlx default: 32)
    prompt_concurrency: int | None = None    # --prompt-concurrency (mlx default: 8)
    prefill_step_size: int | None = None     # --prefill-step-size (mlx default: 2048)
    prompt_cache_size: int | None = None     # --prompt-cache-size
    prompt_cache_bytes: int | None = None    # --prompt-cache-bytes
    max_tokens: int | None = None            # --max-tokens (mlx default: 512)
    temp: float | None = None               # --temp (mlx default: 0.0)
    top_p: float | None = None              # --top-p (mlx default: 1.0)
    top_k: int | None = None               # --top-k (mlx default: 0)
    min_p: float | None = None             # --min-p (mlx default: 0.0)
    draft_model: str | None = None          # --draft-model
    num_draft_tokens: int | None = None     # --num-draft-tokens (mlx default: 3)


@dataclass
class Model:
    source: ModelSource
    runtime_model_id: str = ""
    benchmark_only: bool = False
    disk_gb: float = 0.0
    kv_per_32k_gb: float = 0.0
    ram_gb: float | None = None
    active_params: str = ""
    max_context: int = 0
    serving: ServingMode | str = ""
    notes: str = ""
    extra_args: list[str] | None = None
    enable_thinking: bool | None = None
    server_args: ServerArgs | None = None
    model_info: ModelInfo | None = None


@dataclass
class NodeRuntime:
    type: RuntimeType | str
    port: int
    model_dir: str | None = None


@dataclass(init=False)
class Node:
    host: str
    ram_gb: int
    user: str
    role: NodeRole | str
    fabric_host: bool
    runtime: NodeRuntime | None
    models: list[str]
    # Resolved during pre-flight — None until populated
    platform: str | None
    shell: str | None
    home_dir: str | None
    homebrew_prefix: str | None

    def __init__(
        self,
        host: str | None = None,
        ram_gb: int = 0,
        user: str = "",
        role: NodeRole | str = NodeRole.NODE,
        *,
        ip: str | None = None,
        fabric_host: bool = False,
        runtime: NodeRuntime | None = None,
        models: list[str] | None = None,
        platform: str | None = None,
        shell: str | None = None,
        home_dir: str | None = None,
        homebrew_prefix: str | None = None,
    ) -> None:
        resolved_host = host or ip
        if not resolved_host:
            msg = "Node requires host (or deprecated ip)"
            raise ValueError(msg)
        self.host = resolved_host
        self.ram_gb = ram_gb
        self.user = user
        self.role = role
        self.fabric_host = fabric_host
        self.runtime = runtime
        self.models = models or []
        self.platform = platform
        self.shell = shell
        self.home_dir = home_dir
        self.homebrew_prefix = homebrew_prefix

    @property
    def ip(self) -> str:
        """Deprecated alias for host, kept for internal/backwards compatibility."""
        return self.host

    @ip.setter
    def ip(self, value: str) -> None:
        self.host = value

@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)

    @property
    def compute_nodes(self) -> dict[str, Node]:
        return {k: v for k, v in self.nodes.items() if v.role == NodeRole.NODE}

    @property
    def gateway_name(self) -> str:
        for k, v in self.nodes.items():
            if v.role == NodeRole.GATEWAY:
                return k
        msg = "No gateway node found in config"
        raise ValueError(msg)

    @property
    def gateway(self) -> Node:
        return self.nodes[self.gateway_name]

    # --- Backwards-compatible aliases ---
    @property
    def inference_nodes(self) -> dict[str, Node]:
        return self.compute_nodes

    @property
    def infra_name(self) -> str:
        return self.gateway_name

    @property
    def rock(self) -> Node:
        return self.gateway


def _parse_model_source(raw: dict) -> ModelSource:
    return ModelSource(
        type=raw.get("type", "huggingface"),
        repo=raw.get("repo", ""),
        revision=raw.get("revision", "main"),
        quantize=raw.get("quantize", ""),
        path=raw.get("path", ""),
        package=raw.get("package", ""),
        weight_repo=raw.get("weight_repo", ""),
    )


def _parse_model_info(raw: dict) -> ModelInfo:
    return ModelInfo(
        base_model=raw.get("base_model", ""),
        mode=raw.get("mode", ""),
        input_cost_per_token=raw.get("input_cost_per_token"),
        output_cost_per_token=raw.get("output_cost_per_token"),
        supports_vision=raw.get("supports_vision"),
        supports_function_calling=raw.get("supports_function_calling"),
        supports_parallel_function_calling=raw.get("supports_parallel_function_calling"),
        supports_response_schema=raw.get("supports_response_schema"),
    )


def _parse_server_args(raw: dict) -> ServerArgs:
    return ServerArgs(
        decode_concurrency=raw.get("decode_concurrency"),
        prompt_concurrency=raw.get("prompt_concurrency"),
        prefill_step_size=raw.get("prefill_step_size"),
        prompt_cache_size=raw.get("prompt_cache_size"),
        prompt_cache_bytes=raw.get("prompt_cache_bytes"),
        max_tokens=raw.get("max_tokens"),
        temp=raw.get("temp"),
        top_p=raw.get("top_p"),
        top_k=raw.get("top_k"),
        min_p=raw.get("min_p"),
        draft_model=raw.get("draft_model"),
        num_draft_tokens=raw.get("num_draft_tokens"),
    )


def _parse_node_runtime(raw: dict | None) -> NodeRuntime | None:
    if raw is None:
        return None
    return NodeRuntime(
        type=RuntimeType(raw["type"]),
        port=raw["port"],
        model_dir=raw.get("model_dir"),
    )


def _parse_fabric_host(raw: object, *, node_name: str) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    msg = f"Node '{node_name}': fabric_host must be boolean true/false"
    raise ValueError(msg)


def _default_runtime_model_id(model_id: str, raw: dict) -> str:
    source = raw.get("source", {})
    repo = source.get("repo", "") if isinstance(source, dict) else ""
    return repo.rsplit("/", 1)[-1] if repo else model_id


def _parse_model(model_id: str, raw: dict) -> Model:
    server_args_raw = raw.get("server_args")
    model_info_raw = raw.get("model_info")
    return Model(
        source=_parse_model_source(raw["source"]),
        runtime_model_id=raw.get("runtime_model_id", _default_runtime_model_id(model_id, raw)),
        benchmark_only=raw.get("benchmark_only", False),
        disk_gb=raw.get("disk_gb", 0.0),
        kv_per_32k_gb=raw.get("kv_per_32k_gb", 0.0),
        ram_gb=raw.get("ram_gb"),
        active_params=raw.get("active_params", ""),
        max_context=raw.get("max_context", 0),
        serving=ServingMode(raw["serving"]) if raw.get("serving") else "",
        notes=raw.get("notes", ""),
        extra_args=raw.get("extra_args"),
        enable_thinking=raw.get("enable_thinking"),
        server_args=_parse_server_args(server_args_raw) if server_args_raw is not None else None,
        model_info=_parse_model_info(model_info_raw) if model_info_raw is not None else None,
    )


def parse_cluster_config(raw: dict) -> ClusterConfig:
    """Parse a raw YAML-like dict into a ClusterConfig.

    No file I/O, no .env loading, no user resolution from env vars.
    The user field is stored as-is from the raw dict (empty string if unset).
    """
    models = {k: _parse_model(k, v) for k, v in raw.get("models", {}).items()}

    _ROLE_MIGRATION = {"inference": NodeRole.NODE, "infra": NodeRole.GATEWAY}

    nodes = {}
    for k, v in raw.get("nodes", {}).items():
        raw_role = v.get("role", "node")
        migrated = _ROLE_MIGRATION.get(raw_role)
        role = migrated if migrated else NodeRole(raw_role)
        if migrated:
            import warnings

            # stacklevel=2: when called via load_cluster_config, warning
            # points to the caller of load_cluster_config. Direct callers
            # of parse_cluster_config will see the warning attributed one
            # frame too deep — acceptable since the admin UI is the primary
            # direct caller and doesn't rely on warning attribution.
            warnings.warn(
                f"Node '{k}': role '{raw_role}' is deprecated, use '{role.value}' instead",
                DeprecationWarning,
                stacklevel=2,
            )
        user = v.get("user", "")
        host = v.get("host") or v.get("ip")
        if not host:
            msg = f"Node {k} requires host (or deprecated ip)"
            raise ValueError(msg)
        nodes[k] = Node(
            host=host,
            ram_gb=v["ram_gb"],
            user=user,
            role=role,
            fabric_host=_parse_fabric_host(v.get("fabric_host"), node_name=k),
            runtime=_parse_node_runtime(v.get("runtime")),
            models=list(v.get("models", [])),
            home_dir=v.get("home_dir"),
            homebrew_prefix=v.get("homebrew_prefix"),
        )

    return ClusterConfig(
        models=models,
        nodes=nodes,
    )


def load_cluster_config(path: Path) -> ClusterConfig:
    """Load and parse node-assignments.yaml into a ClusterConfig.

    Thin wrapper around parse_cluster_config that adds .env loading
    and user resolution from environment variables.
    """
    repo_root = find_repo_root()
    env_file = repo_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    with path.open() as f:
        raw = yaml.safe_load(f)

    config = parse_cluster_config(raw)

    # Resolve users from env vars (parse_cluster_config stores as-is)
    for node in config.nodes.values():
        if not node.user:
            if os.environ.get("GATEWAY_SSH_USER"):
                node.user = os.environ["GATEWAY_SSH_USER"]
            else:
                node.user = os.environ.get("USER", "unknown")

    return config


def generate_olla_config(config: ClusterConfig) -> str:
    endpoints: list[dict[str, str | int]] = []
    aliases: dict[str, list[str]] = {}
    seen_nodes: set[str] = set()

    for node_name, node in config.nodes.items():
        if not node.models:
            continue
        runtime = node.runtime
        if runtime is None:
            msg = f"Node '{node_name}' declares models but has no runtime"
            raise ValueError(msg)
        if runtime.type != RuntimeType.OMLX:
            continue
        if node_name not in seen_nodes:
            endpoints.append(
                {
                    "url": f"http://{node.host}:{runtime.port}",
                    "name": f"{node_name}-omlx-live",
                    "type": "openai-compatible",
                    "priority": 100,
                    "model_url": "/v1/models",
                    "health_check_url": "/health",
                    "check_interval": "3s",
                    "check_timeout": "2s",
                }
            )
            seen_nodes.add(node_name)
        for model_id in node.models:
            if model_id not in config.models:
                msg = f"Node '{node_name}' references unknown model '{model_id}'"
                raise ValueError(msg)
            runtime_model_id = config.models[model_id].runtime_model_id
            aliases.setdefault(model_id, [])
            if runtime_model_id not in aliases[model_id]:
                aliases[model_id].append(runtime_model_id)

    output: dict = {
        "server": {
            "host": "127.0.0.1",
            "port": 40115,
            "read_timeout": "20s",
            "write_timeout": "0s",
            "shutdown_timeout": "10s",
            "request_logging": True,
        },
        "proxy": {
            "engine": "olla",
            "profile": "auto",
            "load_balancer": "least-connections",
            "connection_timeout": "5s",
            "response_timeout": "120s",
            "read_timeout": "120s",
            "sticky_sessions": {
                "enabled": True,
                "idle_ttl_seconds": 600,
                "max_sessions": 10000,
                "key_sources": ["session_header", "prefix_hash", "auth_header"],
                "prefix_hash_bytes": 512,
            },
            "retry": {
                "enabled": True,
                "on_connection_failure": True,
                "max_attempts": 0,
            },
        },
        "discovery": {
            "type": "static",
            "refresh_interval": "30s",
            "health_check": {
                "initial_delay": "1s",
            },
            "static": {
                "endpoints": endpoints,
            },
            "model_discovery": {
                "enabled": True,
                "interval": "5m",
                "timeout": "10s",
                "concurrent_workers": 2,
                "retry_attempts": 1,
                "retry_backoff": "1s",
            },
        },
        "model_registry": {
            "type": "memory",
            "enable_unifier": True,
            "routing_strategy": {
                "type": "strict",
                "options": {
                    "fallback_behavior": "compatible_only",
                    "discovery_timeout": "2s",
                    "discovery_refresh_on_miss": False,
                },
            },
        },
        "logging": {
            "level": "info",
            "format": "json",
            "output": "stdout",
        },
        "model_aliases": aliases,
    }
    header = (
        "# AUTO-GENERATED by thunder-forge generate-olla-config\n"
        "# from configs/node-assignments.yaml\n"
        "# Do not edit manually — edit node-assignments.yaml instead.\n\n"
    )
    return header + yaml.dump(output, default_flow_style=False, sort_keys=False)


def find_repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "configs" / "node-assignments.yaml").exists():
            return parent
    msg = "Cannot find repo root (no git repo and no configs/node-assignments.yaml found)"
    raise FileNotFoundError(msg)


def load_config() -> tuple[ClusterConfig, Path]:
    """Load cluster config from the default node-assignments.yaml. Returns (config, path)."""
    root = find_repo_root()
    config_path = root / "configs" / "node-assignments.yaml"
    raw = yaml.safe_load(config_path.read_text())
    return parse_cluster_config(raw), config_path
