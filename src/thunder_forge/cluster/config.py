"""Config parsing, memory validation, and LiteLLM config generation."""

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
class LiteLLMParams:
    max_output_tokens: int | None = None  # 0/None = use default (16384 when max_context set)
    timeout: int | None = None            # per-model timeout seconds; None = use global (120s)
    stream_timeout: int | None = None     # per-model stream timeout; None = inherit global
    weight: int | None = None            # load-balancing weight; None = default (1)
    tpm: int | None = None               # tokens/minute limit; None = unlimited
    rpm: int | None = None               # requests/minute limit; None = unlimited
    temperature: float | None = None     # proxy-level default temperature; None = model default
    max_tokens: int | None = None        # proxy-level default max_tokens; None = model default
    seed: int | None = None              # reproducible outputs; None = non-deterministic


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
    litellm_params: LiteLLMParams | None = None
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
    fabric_host: str | None
    runtime: NodeRuntime | None
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
        fabric_host: str | None = None,
        runtime: NodeRuntime | None = None,
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
class Assignment:
    model: str
    port: int = 0
    embedding: bool = False


@dataclass
class RuntimeRoute:
    model_name: str
    runtime: RuntimeType | str
    node: str
    model: str


@dataclass
class ExternalEndpoint:
    model_name: str
    api_base: str
    api_key_env: str = ""
    api_key: str = ""
    max_input_tokens: int = 0
    max_output_tokens: int = 0


@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    assignments: dict[str, list[Assignment]] = field(default_factory=dict)
    runtime_routes: list[RuntimeRoute] = field(default_factory=list)
    external_endpoints: list[ExternalEndpoint] = field(default_factory=list)

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
        type=raw["type"],
        repo=raw.get("repo", ""),
        revision=raw.get("revision", "main"),
        quantize=raw.get("quantize", ""),
        path=raw.get("path", ""),
        package=raw.get("package", ""),
        weight_repo=raw.get("weight_repo", ""),
    )


def _parse_litellm_params(raw: dict) -> LiteLLMParams:
    return LiteLLMParams(
        max_output_tokens=raw.get("max_output_tokens"),
        timeout=raw.get("timeout"),
        stream_timeout=raw.get("stream_timeout"),
        weight=raw.get("weight"),
        tpm=raw.get("tpm"),
        rpm=raw.get("rpm"),
        temperature=raw.get("temperature"),
        max_tokens=raw.get("max_tokens"),
        seed=raw.get("seed"),
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


def _parse_model(raw: dict) -> Model:
    server_args_raw = raw.get("server_args")
    litellm_params_raw = raw.get("litellm_params")
    model_info_raw = raw.get("model_info")
    return Model(
        source=_parse_model_source(raw["source"]),
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
        litellm_params=_parse_litellm_params(litellm_params_raw) if litellm_params_raw is not None else None,
        model_info=_parse_model_info(model_info_raw) if model_info_raw is not None else None,
    )


def parse_cluster_config(raw: dict) -> ClusterConfig:
    """Parse a raw YAML-like dict into a ClusterConfig.

    No file I/O, no .env loading, no user resolution from env vars.
    The user field is stored as-is from the raw dict (empty string if unset).
    """
    models = {k: _parse_model(v) for k, v in raw.get("models", {}).items()}

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
            fabric_host=v.get("fabric_host"),
            runtime=_parse_node_runtime(v.get("runtime")),
            home_dir=v.get("home_dir"),
            homebrew_prefix=v.get("homebrew_prefix"),
        )

    assignments: dict[str, list[Assignment]] = {}
    for node_name, slots in raw.get("assignments", {}).items():
        assignments[node_name] = [
            Assignment(
                model=s["model"],
                port=s.get("port", 0),
                embedding=s.get("embedding", False),
            )
            for s in slots
        ]

    runtime_routes = [
        RuntimeRoute(
            model_name=route["model_name"],
            runtime=RuntimeType(route["runtime"]),
            node=route["node"],
            model=route["model"],
        )
        for route in raw.get("runtime_routes", [])
    ]

    external_endpoints = [
        ExternalEndpoint(
            model_name=ep["model_name"],
            api_base=ep["api_base"],
            api_key_env=ep.get("api_key_env", ""),
            api_key=ep.get("api_key", ""),
            max_input_tokens=ep.get("max_input_tokens", 0),
            max_output_tokens=ep.get("max_output_tokens", 0),
        )
        for ep in raw.get("external_endpoints", [])
    ]

    return ClusterConfig(
        models=models,
        nodes=nodes,
        assignments=assignments,
        runtime_routes=runtime_routes,
        external_endpoints=external_endpoints,
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


OS_OVERHEAD_GB = 8


def validate_memory(config: ClusterConfig) -> list[str]:
    errors: list[str] = []
    for node_name, slots in config.assignments.items():
        node = config.nodes.get(node_name)
        if node is None:
            errors.append(f"{node_name}: node not found in config")
            continue
        parts: list[str] = []
        total = OS_OVERHEAD_GB
        for slot in slots:
            model = config.models.get(slot.model)
            if model is None:
                errors.append(f"{node_name}: model '{slot.model}' not found in registry")
                continue
            weight_gb = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv_gb = model.kv_per_32k_gb
            slot_total = weight_gb + kv_gb
            total += slot_total
            parts.append(f"{slot.model}({weight_gb}+{kv_gb}kv)")
        budget_str = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} GB / {node.ram_gb} GB"
        if total > node.ram_gb:
            errors.append(f"{node_name}: {budget_str} ❌ EXCEEDS")
    return errors


def _build_embedding_entry(model_name: str, model: Model, node_ip: str, port: int) -> dict:
    """Build a LiteLLM model_list entry for an embedding model."""
    entry: dict = {
        "model_name": model_name,
        "litellm_params": {
            "model": f"openai/{model.source.repo}",
            "api_base": f"http://{node_ip}:{port}/v1",
            "api_key": "none",
            "drop_params": True,
            "encoding_format": "float",
        },
        "model_info": {
            "mode": "embedding",
        },
    }
    if model.max_context > 0:
        entry["litellm_params"]["max_input_tokens"] = model.max_context
    mi = model.model_info
    if mi:
        if mi.input_cost_per_token is not None:
            entry["model_info"]["input_cost_per_token"] = mi.input_cost_per_token
        if mi.output_cost_per_token is not None:
            entry["model_info"]["output_cost_per_token"] = mi.output_cost_per_token
    return entry


def generate_litellm_config(config: ClusterConfig) -> str:
    model_list: list[dict] = []
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            model = config.models[slot.model]
            if model.serving == ServingMode.CLI:
                continue
            if model.serving == ServingMode.EMBEDDING:
                model_list.append(_build_embedding_entry(slot.model, model, node.ip, slot.port))
                continue
            entry: dict = {
                "model_name": slot.model,
                "litellm_params": {
                    "model": f"openai/{model.source.repo}",
                    "api_base": f"http://{node.ip}:{slot.port}/v1",
                    "api_key": "none",
                },
            }
            lp = model.litellm_params
            if model.max_context > 0:
                entry["litellm_params"]["max_input_tokens"] = model.max_context
            # max_output_tokens: use explicit value, or default to 16384 when max_context is set
            if lp and lp.max_output_tokens:
                entry["litellm_params"]["max_output_tokens"] = lp.max_output_tokens
            elif model.max_context > 0:
                entry["litellm_params"]["max_output_tokens"] = 16384
            if lp:
                if lp.timeout:
                    entry["litellm_params"]["timeout"] = lp.timeout
                if lp.stream_timeout:
                    entry["litellm_params"]["stream_timeout"] = lp.stream_timeout
                if lp.weight:
                    entry["litellm_params"]["weight"] = lp.weight
                if lp.tpm:
                    entry["litellm_params"]["tpm"] = lp.tpm
                if lp.rpm:
                    entry["litellm_params"]["rpm"] = lp.rpm
                if lp.temperature is not None:
                    entry["litellm_params"]["temperature"] = lp.temperature
                if lp.max_tokens:
                    entry["litellm_params"]["max_tokens"] = lp.max_tokens
                if lp.seed is not None:
                    entry["litellm_params"]["seed"] = lp.seed
            mi = model.model_info
            if mi:
                info: dict = {}
                if mi.base_model:
                    info["base_model"] = mi.base_model
                if mi.mode:
                    info["mode"] = mi.mode
                if mi.input_cost_per_token is not None:
                    info["input_cost_per_token"] = mi.input_cost_per_token
                if mi.output_cost_per_token is not None:
                    info["output_cost_per_token"] = mi.output_cost_per_token
                if mi.supports_vision is not None:
                    info["supports_vision"] = mi.supports_vision
                if mi.supports_function_calling is not None:
                    info["supports_function_calling"] = mi.supports_function_calling
                if mi.supports_parallel_function_calling is not None:
                    info["supports_parallel_function_calling"] = mi.supports_parallel_function_calling
                if mi.supports_response_schema is not None:
                    info["supports_response_schema"] = mi.supports_response_schema
                if info:
                    entry["model_info"] = info
            model_list.append(entry)
            if slot.embedding:
                emb_model = config.models.get("embedding")
                emb_name = "embedding"
                if not emb_model:
                    for mname, mobj in config.models.items():
                        if mobj.serving == ServingMode.EMBEDDING:
                            emb_model = mobj
                            emb_name = mname
                            break
                if emb_model:
                    model_list.append(_build_embedding_entry(emb_name, emb_model, node.ip, slot.port))
    for route in config.runtime_routes:
        if route.runtime != RuntimeType.OMLX:
            continue
        node = config.nodes[route.node]
        if node.runtime is None:
            msg = f"Runtime route '{route.model_name}' references node '{route.node}' without runtime"
            raise ValueError(msg)
        model_list.append(
            {
                "model_name": route.model_name,
                "litellm_params": {
                    "model": f"openai/{route.model}",
                    "api_base": f"http://{node.host}:{node.runtime.port}/v1",
                    "api_key": "dummy",
                },
            }
        )
    for ep in config.external_endpoints:
        entry = {
            "model_name": ep.model_name,
            "litellm_params": {
                "model": f"openai/{ep.model_name}",
                "api_base": ep.api_base,
            },
        }
        if ep.api_key_env:
            entry["litellm_params"]["api_key"] = f"os.environ/{ep.api_key_env}"
        elif ep.api_key:
            entry["litellm_params"]["api_key"] = ep.api_key
        if ep.max_input_tokens:
            entry["litellm_params"]["max_input_tokens"] = ep.max_input_tokens
        if ep.max_output_tokens:
            entry["litellm_params"]["max_output_tokens"] = ep.max_output_tokens
        model_list.append(entry)

    output: dict = {
        "model_list": model_list,
        "litellm_settings": {
            "num_retries": 2,
            "timeout": 120,
            "allowed_fails": 3,
            "cooldown_time": 30,
            "use_chat_completions_url_for_anthropic_messages": True,
            "callbacks": ["prometheus"],
        },
        "router_settings": {
            "routing_strategy": "least-busy",
            "model_group_retry_policy": {},
        },
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
        },
    }
    header = (
        "# AUTO-GENERATED by thunder-forge generate-config\n"
        "# from configs/node-assignments.yaml\n"
        "# Do not edit manually — edit node-assignments.yaml instead.\n\n"
    )
    return header + yaml.dump(output, default_flow_style=False, sort_keys=False)


def generate_olla_config(config: ClusterConfig) -> str:
    endpoints: list[dict[str, str | int]] = []
    aliases: dict[str, list[str]] = {}
    seen_nodes: set[str] = set()

    for route in config.runtime_routes:
        if route.runtime != RuntimeType.OMLX:
            continue
        node = config.nodes[route.node]
        runtime = node.runtime
        if runtime is None:
            msg = f"Runtime route '{route.model_name}' references node '{route.node}' without runtime"
            raise ValueError(msg)
        if route.node not in seen_nodes:
            endpoints.append(
                {
                    "url": f"http://{node.host}:{runtime.port}",
                    "name": f"{route.node}-omlx-live",
                    "type": "openai-compatible",
                    "priority": 100,
                    "model_url": "/v1/models",
                    "health_check_url": "/health",
                    "check_interval": "3s",
                    "check_timeout": "2s",
                }
            )
            seen_nodes.add(route.node)
        aliases.setdefault(route.model_name, [])
        if route.model not in aliases[route.model_name]:
            aliases[route.model_name].append(route.model)

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


def check_config_sync(config: ClusterConfig, committed_path: Path) -> bool:
    generated = generate_litellm_config(config)
    if not committed_path.exists():
        return False
    committed = committed_path.read_text()
    return generated == committed


def load_config() -> tuple[ClusterConfig, Path]:
    """Load cluster config from the default node-assignments.yaml. Returns (config, path)."""
    root = find_repo_root()
    config_path = root / "configs" / "node-assignments.yaml"
    raw = yaml.safe_load(config_path.read_text())
    return parse_cluster_config(raw), config_path
