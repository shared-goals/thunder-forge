"""Config parsing, memory validation, and Olla config generation."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv

from thunder_forge.cluster.ports import (
    DEFAULT_EDGE_PORT,
    DEFAULT_OLLA_PORT,
    DEFAULT_OMLX_PORT,
    parse_port,
    resolve_port,
)

TF_CONFIG_FILENAME = "tfconfig.yaml"
TF_CONFIG_EXAMPLE_FILENAME = "tfconfig.example.yaml"
GENERATED_CONFIG_DIR = "config"
GENERATED_OLLA_CONFIG_FILENAME = "olla-config.yaml"
DEFAULT_EDGE_ACCESS_LOG = "logs/tf-edge-access.jsonl"
DEFAULT_LOG_RETENTION_DAYS = 3
DEFAULT_EDGE_HOST = "0.0.0.0"
DEFAULT_OLLA_VERSION = "v0.0.27"
DEFAULT_OLLA_OS = "macos"
DEFAULT_OLLA_ARCH = "arm64"
DEFAULT_OLLA_BIN_DIR = "olla-bin"
DEFAULT_SYNC_TRANSPORT = "auto"
DEFAULT_SYNC_TIMEOUT = 7200
DEFAULT_SYNC_RESTART_RUNTIME = True
DEFAULT_SMOKE_TIMEOUT = 30.0


class RuntimeType(StrEnum):
    OMLX = "omlx"


class NodeRole(StrEnum):
    GATEWAY = "gateway"
    CACHE = "cache"
    INFERENCE = "inference"


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
    base_model: str = ""  # maps custom names to known models for token counting
    mode: str = ""  # chat, completion, embedding, image_generation
    input_cost_per_token: float | None = None  # cost tracking / budget enforcement
    output_cost_per_token: float | None = None  # cost tracking / budget enforcement
    supports_vision: bool | None = None  # multimodal routing
    supports_function_calling: bool | None = None  # tool use routing
    supports_parallel_function_calling: bool | None = None  # parallel tool calls
    supports_response_schema: bool | None = None  # structured output support


@dataclass
class ServerArgs:
    decode_concurrency: int | None = None  # --decode-concurrency (mlx default: 32)
    prompt_concurrency: int | None = None  # --prompt-concurrency (mlx default: 8)
    prefill_step_size: int | None = None  # --prefill-step-size (mlx default: 2048)
    prompt_cache_size: int | None = None  # --prompt-cache-size
    prompt_cache_bytes: int | None = None  # --prompt-cache-bytes
    max_tokens: int | None = None  # --max-tokens (mlx default: 512)
    temp: float | None = None  # --temp (mlx default: 0.0)
    top_p: float | None = None  # --top-p (mlx default: 1.0)
    top_k: int | None = None  # --top-k (mlx default: 0)
    min_p: float | None = None  # --min-p (mlx default: 0.0)
    draft_model: str | None = None  # --draft-model
    num_draft_tokens: int | None = None  # --num-draft-tokens (mlx default: 3)


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
    bind_host: str = "0.0.0.0"
    base_path: str | None = None
    log_level: str | None = None
    max_model_memory: str | None = None
    max_process_memory: str | None = None
    max_concurrent_requests: int | None = None
    paged_ssd_cache_dir: str | None = None
    paged_ssd_cache_max_size: str | None = None
    hot_cache_max_size: str | None = None
    no_cache: bool = False
    mcp_config: str | None = None
    hf_endpoint: str | None = None
    trusted_network: bool = False


@dataclass(frozen=True)
class ConfigLintIssue:
    severity: str
    path: str
    message: str


@dataclass
class ServiceConfig:
    edge_host: str = DEFAULT_EDGE_HOST
    edge_port: int = DEFAULT_EDGE_PORT
    olla_port: int = DEFAULT_OLLA_PORT
    olla_version: str = DEFAULT_OLLA_VERSION
    olla_version_pinned: bool = True
    olla_os: str = DEFAULT_OLLA_OS
    olla_arch: str = DEFAULT_OLLA_ARCH
    olla_bin_dir: str = DEFAULT_OLLA_BIN_DIR
    olla_local_binary: str = ""
    omlx_port: int = DEFAULT_OMLX_PORT
    edge_access_log: str = DEFAULT_EDGE_ACCESS_LOG
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    frontend_admin_user: str = ""


@dataclass
class OperationSmokeConfig:
    alias: str = ""
    model: str = ""
    client_id: str = ""
    timeout: float = DEFAULT_SMOKE_TIMEOUT


@dataclass
class OperationSyncConfig:
    transport: str = DEFAULT_SYNC_TRANSPORT
    timeout: int = DEFAULT_SYNC_TIMEOUT
    restart_runtime: bool = DEFAULT_SYNC_RESTART_RUNTIME


@dataclass
class OperationConfig:
    smoke: OperationSmokeConfig = field(default_factory=OperationSmokeConfig)
    sync: OperationSyncConfig = field(default_factory=OperationSyncConfig)


@dataclass(init=False)
class Node:
    host: str
    ram_gb: int
    user: str
    admin_user: str
    roles: list[NodeRole]
    fabric_host: bool
    runtime: NodeRuntime | None
    models: list[str]
    # Resolved during pre-flight — None until populated
    platform: str | None
    shell: str | None
    home_dir: str | None
    homebrew_prefix: str | None
    # Optional public hostname (e.g., NetBird cloud hostname)
    public_host: str | None = None

    def __init__(
        self,
        host: str | None = None,
        ram_gb: int = 0,
        user: str = "",
        admin_user: str = "",
        roles: list[NodeRole] | None = None,
        *,
        fabric_host: bool = False,
        runtime: NodeRuntime | None = None,
        models: list[str] | None = None,
        platform: str | None = None,
        shell: str | None = None,
        home_dir: str | None = None,
        homebrew_prefix: str | None = None,
        public_host: str | None = None,
    ) -> None:
        if not host:
            msg = "Node requires host"
            raise ValueError(msg)
        self.host = host
        self.ram_gb = ram_gb
        self.user = user
        self.admin_user = admin_user
        self.roles = roles or [NodeRole.INFERENCE]
        self.fabric_host = fabric_host
        self.runtime = runtime
        self.models = models or []
        self.platform = platform
        self.shell = shell
        self.home_dir = home_dir
        self.homebrew_prefix = homebrew_prefix
        self.public_host = public_host

    @property
    def role(self) -> NodeRole:
        return self.roles[0]

    def has_role(self, role: NodeRole) -> bool:
        return role in self.roles


@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    services: ServiceConfig = field(default_factory=ServiceConfig)
    operations: OperationConfig = field(default_factory=OperationConfig)

    @property
    def compute_nodes(self) -> dict[str, Node]:
        return {k: v for k, v in self.nodes.items() if v.has_role(NodeRole.INFERENCE)}

    @property
    def gateway_name(self) -> str:
        for k, v in self.nodes.items():
            if v.has_role(NodeRole.GATEWAY):
                return k
        msg = "No gateway node found in config"
        raise ValueError(msg)

    @property
    def gateway(self) -> Node:
        return self.nodes[self.gateway_name]


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


def _parse_service_port(raw: dict, service_name: str, default: int) -> int:
    service_raw = raw.get(service_name, {})
    if service_raw is None:
        return default
    if not isinstance(service_raw, dict):
        msg = f"services.{service_name} must be a mapping"
        raise ValueError(msg)
    return parse_port(service_raw.get("port", default), name=f"services.{service_name}.port")


def _parse_positive_int(raw: object, *, name: str, default: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        msg = f"{name} must be an integer"
        raise ValueError(msg)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be an integer"
        raise ValueError(msg) from exc
    if value <= 0:
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return value


def _parse_positive_float(raw: object, *, name: str, default: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a number"
        raise ValueError(msg) from exc
    if value <= 0:
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return value


def _parse_bool(raw: object, *, name: str, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    msg = f"{name} must be boolean true/false"
    raise ValueError(msg)


def _parse_optional_positive_int(raw: object, *, name: str) -> int | None:
    if raw is None:
        return None
    return _parse_positive_int(raw, name=name, default=1)


def _parse_optional_non_empty_string(raw: object, *, name: str, default: str | None = None) -> str | None:
    if raw is None:
        return default
    if not isinstance(raw, str):
        msg = f"{name} must be a string"
        raise ValueError(msg)
    value = raw.strip()
    if not value:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    return value


def _parse_transport(raw: object, *, name: str, default: str) -> str:
    value = str(raw or default).strip()
    if value not in {"auto", "fabric", "management"}:
        msg = f"{name} must be one of: auto, fabric, management"
        raise ValueError(msg)
    return value


def _parse_services(raw: object) -> ServiceConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = "services must be a mapping"
        raise ValueError(msg)
    edge_raw = raw.get("edge", {}) or {}
    if not isinstance(edge_raw, dict):
        msg = "services.edge must be a mapping"
        raise ValueError(msg)
    edge_access_log = str(edge_raw.get("access_log", DEFAULT_EDGE_ACCESS_LOG)).strip()
    if not edge_access_log:
        msg = "services.edge.access_log must not be empty"
        raise ValueError(msg)
    log_retention_days = _parse_positive_int(
        raw.get("log_retention_days"),
        name="services.log_retention_days",
        default=DEFAULT_LOG_RETENTION_DAYS,
    )
    edge_host = str(edge_raw.get("host", DEFAULT_EDGE_HOST)).strip()
    if not edge_host:
        msg = "services.edge.host must not be empty"
        raise ValueError(msg)
    frontend_raw = raw.get("frontend", {}) or {}
    if not isinstance(frontend_raw, dict):
        msg = "services.frontend must be a mapping"
        raise ValueError(msg)
    frontend_admin_user = str(frontend_raw.get("admin_user", "")).strip()
    olla_raw = raw.get("olla", {}) or {}
    if not isinstance(olla_raw, dict):
        msg = "services.olla must be a mapping"
        raise ValueError(msg)
    has_olla_block = "olla" in raw and raw.get("olla") is not None
    olla_version_raw = str(olla_raw.get("version", "")).strip()
    olla_version_pinned = bool(olla_version_raw) or not has_olla_block
    olla_version = olla_version_raw or DEFAULT_OLLA_VERSION
    return ServiceConfig(
        edge_host=edge_host,
        edge_port=_parse_service_port(raw, "edge", DEFAULT_EDGE_PORT),
        olla_port=_parse_service_port(raw, "olla", DEFAULT_OLLA_PORT),
        olla_version=olla_version,
        olla_version_pinned=olla_version_pinned,
        olla_os=str(olla_raw.get("os", DEFAULT_OLLA_OS)).strip() or DEFAULT_OLLA_OS,
        olla_arch=str(olla_raw.get("arch", DEFAULT_OLLA_ARCH)).strip() or DEFAULT_OLLA_ARCH,
        olla_bin_dir=str(olla_raw.get("bin_dir", DEFAULT_OLLA_BIN_DIR)).strip() or DEFAULT_OLLA_BIN_DIR,
        olla_local_binary=_parse_optional_non_empty_string(
            olla_raw.get("local_binary"),
            name="services.olla.local_binary",
            default="",
        )
        or "",
        omlx_port=_parse_service_port(raw, "omlx", DEFAULT_OMLX_PORT),
        edge_access_log=edge_access_log,
        log_retention_days=log_retention_days,
        frontend_admin_user=frontend_admin_user,
    )


def _parse_operations(raw: object) -> OperationConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = "operations must be a mapping"
        raise ValueError(msg)
    smoke_raw = raw.get("smoke", {}) or {}
    if not isinstance(smoke_raw, dict):
        msg = "operations.smoke must be a mapping"
        raise ValueError(msg)
    sync_raw = raw.get("sync", {}) or {}
    if not isinstance(sync_raw, dict):
        msg = "operations.sync must be a mapping"
        raise ValueError(msg)
    return OperationConfig(
        smoke=OperationSmokeConfig(
            alias=str(smoke_raw.get("alias", "")).strip(),
            model=str(smoke_raw.get("model", "")).strip(),
            client_id=str(smoke_raw.get("client_id", "")).strip(),
            timeout=_parse_positive_float(
                smoke_raw.get("timeout"),
                name="operations.smoke.timeout",
                default=DEFAULT_SMOKE_TIMEOUT,
            ),
        ),
        sync=OperationSyncConfig(
            transport=_parse_transport(
                sync_raw.get("transport"),
                name="operations.sync.transport",
                default=DEFAULT_SYNC_TRANSPORT,
            ),
            timeout=_parse_positive_int(
                sync_raw.get("timeout"),
                name="operations.sync.timeout",
                default=DEFAULT_SYNC_TIMEOUT,
            ),
            restart_runtime=_parse_bool(
                sync_raw.get("restart_runtime"),
                name="operations.sync.restart_runtime",
                default=DEFAULT_SYNC_RESTART_RUNTIME,
            ),
        ),
    )


def _parse_node_runtime(
    raw: object,
    *,
    node_name: str,
    default_port: int = DEFAULT_OMLX_PORT,
) -> NodeRuntime | None:
    if raw is None:
        return None
    path = f"nodes.{node_name}.runtime"
    if not isinstance(raw, dict):
        msg = f"{path} must be a mapping"
        raise ValueError(msg)
    runtime_type_raw = _parse_optional_non_empty_string(raw.get("type"), name=f"{path}.type")
    if runtime_type_raw is None:
        msg = f"{path}.type is required"
        raise ValueError(msg)
    try:
        runtime_type = RuntimeType(runtime_type_raw)
    except ValueError as exc:
        msg = f"{path}.type must be one of: {', '.join(item.value for item in RuntimeType)}"
        raise ValueError(msg) from exc
    return NodeRuntime(
        type=runtime_type,
        port=parse_port(raw.get("port", default_port), name=f"{path}.port"),
        model_dir=_parse_optional_non_empty_string(raw.get("model_dir"), name=f"{path}.model_dir"),
        bind_host=_parse_optional_non_empty_string(raw.get("bind_host"), name=f"{path}.bind_host", default="0.0.0.0")
        or "0.0.0.0",
        base_path=_parse_optional_non_empty_string(raw.get("base_path"), name=f"{path}.base_path"),
        log_level=_parse_optional_non_empty_string(raw.get("log_level"), name=f"{path}.log_level"),
        max_model_memory=_parse_optional_non_empty_string(raw.get("max_model_memory"), name=f"{path}.max_model_memory"),
        max_process_memory=_parse_optional_non_empty_string(
            raw.get("max_process_memory"),
            name=f"{path}.max_process_memory",
        ),
        max_concurrent_requests=_parse_optional_positive_int(
            raw.get("max_concurrent_requests"),
            name=f"{path}.max_concurrent_requests",
        ),
        paged_ssd_cache_dir=_parse_optional_non_empty_string(
            raw.get("paged_ssd_cache_dir"),
            name=f"{path}.paged_ssd_cache_dir",
        ),
        paged_ssd_cache_max_size=_parse_optional_non_empty_string(
            raw.get("paged_ssd_cache_max_size"),
            name=f"{path}.paged_ssd_cache_max_size",
        ),
        hot_cache_max_size=_parse_optional_non_empty_string(
            raw.get("hot_cache_max_size"),
            name=f"{path}.hot_cache_max_size",
        ),
        no_cache=_parse_bool(raw.get("no_cache"), name=f"{path}.no_cache", default=False),
        mcp_config=_parse_optional_non_empty_string(raw.get("mcp_config"), name=f"{path}.mcp_config"),
        hf_endpoint=_parse_optional_non_empty_string(raw.get("hf_endpoint"), name=f"{path}.hf_endpoint"),
        trusted_network=_parse_bool(raw.get("trusted_network"), name=f"{path}.trusted_network", default=False),
    )


def _parse_fabric_host(raw: object, *, node_name: str) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    msg = f"Node '{node_name}': fabric_host must be boolean true/false"
    raise ValueError(msg)


def _parse_node_roles(raw: dict, *, node_name: str) -> list[NodeRole]:
    if "role" in raw:
        msg = f"Node '{node_name}': 'role' is not supported; use 'roles: [...]'"
        raise ValueError(msg)
    roles_raw: object = raw.get("roles", [NodeRole.INFERENCE])
    if not isinstance(roles_raw, list) or not roles_raw:
        msg = f"Node '{node_name}': roles must be a non-empty list"
        raise ValueError(msg)
    roles: list[NodeRole] = []
    for item in roles_raw:
        role = NodeRole(str(item).strip())
        if role not in roles:
            roles.append(role)
    return roles


def _default_runtime_model_id(model_id: str, raw: dict) -> str:
    source = raw.get("source", {})
    repo = source.get("repo", "") if isinstance(source, dict) else ""
    return repo.rsplit("/", 1)[-1] if repo else model_id


def _parse_model(model_id: str, raw: dict) -> Model:
    if "serving" in raw:
        msg = f"models.{model_id}.serving is not supported; use model_info.mode for catalog metadata"
        raise ValueError(msg)
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
    services = _parse_services(raw.get("services", {}))
    operations = _parse_operations(raw.get("operations", {}))
    models = {k: _parse_model(k, v) for k, v in raw.get("models", {}).items()}

    nodes = {}
    for k, v in raw.get("nodes", {}).items():
        roles = _parse_node_roles(v, node_name=k)
        user = v.get("user", "")
        host = v.get("host")
        if not host:
            msg = f"Node {k} requires host"
            raise ValueError(msg)
        nodes[k] = Node(
            host=host,
            ram_gb=v["ram_gb"],
            user=user,
            admin_user=str(v.get("admin_user", "")).strip(),
            roles=roles,
            fabric_host=_parse_fabric_host(v.get("fabric_host"), node_name=k),
            runtime=_parse_node_runtime(v.get("runtime"), node_name=k, default_port=services.omlx_port),
            models=list(v.get("models", [])),
            platform=v.get("platform"),
            shell=v.get("shell"),
            home_dir=v.get("home_dir"),
            homebrew_prefix=v.get("homebrew_prefix"),
            public_host=v.get("public_host"),
        )

    return ClusterConfig(
        models=models,
        nodes=nodes,
        services=services,
        operations=operations,
    )


def load_cluster_config(path: Path) -> ClusterConfig:
    """Load and parse a Thunder Forge cluster config into a ClusterConfig.

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


def generate_olla_config(config: ClusterConfig, *, port: int | None = None, repo_root: Path | None = None) -> str:
    olla_port = resolve_port(port, default=config.services.olla_port)
    log_output_path = str((repo_root / "logs" / "olla.log").resolve()) if repo_root else "../logs/olla.log"
    endpoints: list[dict[str, str | int]] = []
    aliases: dict[str, list[str]] = {}
    seen_nodes: set[str] = set()

    for node_name, node in config.nodes.items():
        if not node.models:
            continue
        if not node.has_role(NodeRole.INFERENCE):
            msg = f"Node '{node_name}' declares models but is not an inference node"
            raise ValueError(msg)
        runtime = node.runtime
        if runtime is None:
            msg = f"Node '{node_name}' declares models but has no runtime"
            raise ValueError(msg)
        if runtime.type != RuntimeType.OMLX:
            continue
        endpoint_runtime_ids: list[str] = []
        for model_id in node.models:
            if model_id not in config.models:
                msg = f"Node '{node_name}' references unknown model '{model_id}'"
                raise ValueError(msg)
            runtime_model_id = config.models[model_id].runtime_model_id
            if runtime_model_id not in endpoint_runtime_ids:
                endpoint_runtime_ids.append(runtime_model_id)
        if node_name not in seen_nodes:
            endpoints.append(
                {
                    "url": f"http://{node.host}:{runtime.port}",
                    "name": f"{node_name}-omlx-live",
                    "type": "omlx",
                    "priority": 100,
                    "model_url": "/v1/models",
                    "health_check_url": "/health",
                    "check_interval": "3s",
                    "check_timeout": "2s",
                    # Restrict model discovery to assigned runtime ids for this endpoint.
                    # This prevents stale/local oMLX models from becoming routable.
                    "model_filter": {"include": endpoint_runtime_ids},
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
            "port": olla_port,
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
                "key_sources": ["session_header", "auth_header", "prefix_hash"],
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
            "output": log_output_path,
        },
        "model_aliases": aliases,
    }
    header = (
        "# AUTO-GENERATED by thunder-forge generate-olla-config\n"
        f"# from {TF_CONFIG_FILENAME}\n"
        f"# Do not edit manually — edit {TF_CONFIG_FILENAME} instead.\n\n"
    )
    return header + yaml.dump(output, default_flow_style=False, sort_keys=False)


def lint_cluster_config(config: ClusterConfig) -> list[ConfigLintIssue]:
    """Return actionable config issues without generating derived router state."""
    issues: list[ConfigLintIssue] = []
    runtime_model_ids: dict[str, str] = {}

    for model_id, model in config.models.items():
        if model.runtime_model_id in runtime_model_ids:
            issues.append(
                ConfigLintIssue(
                    severity="warning",
                    path=f"models.{model_id}.runtime_model_id",
                    message=f"runtime model id also used by '{runtime_model_ids[model.runtime_model_id]}'",
                )
            )
        else:
            runtime_model_ids[model.runtime_model_id] = model_id

    gateway_nodes = [node for node in config.nodes.values() if node.has_role(NodeRole.GATEWAY)]
    inference_nodes = [node for node in config.nodes.values() if node.has_role(NodeRole.INFERENCE)]
    if not gateway_nodes:
        issues.append(
            ConfigLintIssue(
                severity="error",
                path="nodes",
                message="no gateway node configured",
            )
        )
    if not inference_nodes:
        issues.append(
            ConfigLintIssue(
                severity="error",
                path="nodes",
                message="no inference node configured",
            )
        )
    elif not any(
        node.runtime is not None
        and node.runtime.type == RuntimeType.OMLX
        and any(model_id in config.models for model_id in node.models)
        for node in inference_nodes
    ):
        issues.append(
            ConfigLintIssue(
                severity="error",
                path="nodes",
                message="no routable model placements configured for inference nodes",
            )
        )

    for node_name, node in config.nodes.items():
        if node.models and node.runtime is None:
            issues.append(
                ConfigLintIssue(
                    severity="error",
                    path=f"nodes.{node_name}.runtime",
                    message="node declares models but has no runtime",
                )
            )
        if node.runtime is not None and node.runtime.type == RuntimeType.OMLX:
            if node.runtime.port <= 0 or node.runtime.port > 65535:
                issues.append(
                    ConfigLintIssue(
                        severity="error",
                        path=f"nodes.{node_name}.runtime.port",
                        message="runtime port must be between 1 and 65535",
                    )
                )
            if node.runtime.bind_host == "0.0.0.0" and not node.runtime.trusted_network:
                issues.append(
                    ConfigLintIssue(
                        severity="warning",
                        path=f"nodes.{node_name}.runtime",
                        message="oMLX runtime binds 0.0.0.0 without trusted_network: true",
                    )
                )
        for model_id in node.models:
            if model_id not in config.models:
                issues.append(
                    ConfigLintIssue(
                        severity="error",
                        path=f"nodes.{node_name}.models",
                        message=f"unknown model '{model_id}'",
                    )
                )
                continue
            if config.models[model_id].benchmark_only:
                issues.append(
                    ConfigLintIssue(
                        severity="warning",
                        path=f"nodes.{node_name}.models",
                        message=f"benchmark-only model '{model_id}' is assigned to node",
                    )
                )

    return issues


def default_cluster_config_path(repo_root: Path) -> Path:
    """Return the local Thunder Forge cluster config path."""
    return repo_root / TF_CONFIG_FILENAME


def generated_olla_config_path(repo_root: Path) -> Path:
    return repo_root / GENERATED_CONFIG_DIR / GENERATED_OLLA_CONFIG_FILENAME


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
        if (parent / TF_CONFIG_FILENAME).exists() or (parent / TF_CONFIG_EXAMPLE_FILENAME).exists():
            return parent
    msg = f"Cannot find repo root (no git repo and no {TF_CONFIG_FILENAME} found)"
    raise FileNotFoundError(msg)


def load_config() -> tuple[ClusterConfig, Path]:
    """Load cluster config from the default tfconfig.yaml. Returns (config, path)."""
    root = find_repo_root()
    config_path = default_cluster_config_path(root)
    raw = yaml.safe_load(config_path.read_text())
    return parse_cluster_config(raw), config_path
