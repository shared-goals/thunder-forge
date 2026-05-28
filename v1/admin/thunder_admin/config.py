"""Config CRUD, JSONB<->YAML serialization, and cross-entity validation."""

from __future__ import annotations

from collections import OrderedDict

import yaml

from thunder_forge.cluster.config import parse_cluster_config, validate_memory

_TOP_KEY_ORDER = ["models", "nodes", "assignments", "external_endpoints"]
_MODEL_KEY_ORDER = [
    "source",
    "disk_gb",
    "ram_gb",
    "kv_per_32k_gb",
    "active_params",
    "max_context",
    "enable_thinking",
    "extra_args",
    "serving",
    "notes",
]
_SOURCE_KEY_ORDER = [
    "type",
    "repo",
    "revision",
    "quantize",
    "path",
    "package",
    "weight_repo",
]
_NODE_KEY_ORDER = ["ip", "ram_gb", "role", "user"]
_ASSIGNMENT_KEY_ORDER = ["model", "port", "embedding"]
_ENDPOINT_KEY_ORDER = [
    "model_name",
    "api_base",
    "api_key_env",
    "api_key",
    "max_input_tokens",
    "max_output_tokens",
]


def _ordered(d: dict, key_order: list[str]) -> OrderedDict:
    """Reorder a dict according to key_order, appending unknown keys at the end."""
    result = OrderedDict()
    for k in key_order:
        if k in d:
            result[k] = d[k]
    for k in d:
        if k not in result:
            result[k] = d[k]
    return result


def _order_config(config: dict) -> OrderedDict:
    """Deep-order a config dict for consistent YAML output."""
    result = OrderedDict()

    if "models" in config:
        models = OrderedDict()
        for name, model in config["models"].items():
            m = _ordered(model, _MODEL_KEY_ORDER)
            if "source" in m and isinstance(m["source"], dict):
                m["source"] = _ordered(m["source"], _SOURCE_KEY_ORDER)
            models[name] = m
        result["models"] = models

    if "nodes" in config:
        nodes = OrderedDict()
        for name, node in config["nodes"].items():
            nodes[name] = _ordered(node, _NODE_KEY_ORDER)
        result["nodes"] = nodes

    if "assignments" in config:
        assignments = OrderedDict()
        for node_name, slots in config["assignments"].items():
            assignments[node_name] = [_ordered(s, _ASSIGNMENT_KEY_ORDER) for s in slots]
        result["assignments"] = assignments

    if "external_endpoints" in config:
        result["external_endpoints"] = [_ordered(ep, _ENDPOINT_KEY_ORDER) for ep in config["external_endpoints"]]

    for k in config:
        if k not in result:
            result[k] = config[k]

    return result


yaml.add_representer(
    OrderedDict,
    lambda dumper, data: dumper.represent_mapping("tag:yaml.org,2002:map", data.items()),
)


def _represent_float(dumper: yaml.Dumper, value: float) -> yaml.ScalarNode:
    """Force decimal notation for small floats instead of scientific notation."""
    if value != value:  # NaN
        return dumper.represent_scalar("tag:yaml.org,2002:float", ".nan")
    if value == float("inf"):
        return dumper.represent_scalar("tag:yaml.org,2002:float", ".inf")
    if value == float("-inf"):
        return dumper.represent_scalar("tag:yaml.org,2002:float", "-.inf")
    # Use enough decimal places to preserve precision, strip trailing zeros
    text = f"{value:.10f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return dumper.represent_scalar("tag:yaml.org,2002:float", text)


yaml.add_representer(float, _represent_float)


def jsonb_to_yaml(config_json: dict) -> str:
    """Convert a JSONB config dict to YAML with fixed key order."""
    ordered = _order_config(config_json)
    return yaml.dump(ordered, default_flow_style=False, allow_unicode=True, sort_keys=False)


def validate_config(config_json: dict) -> list[str]:
    """Validate a config dict for cross-entity consistency."""
    errors: list[str] = []

    models = config_json.get("models", {})
    nodes = config_json.get("nodes", {})
    assignments = config_json.get("assignments", {})

    for node_name, slots in assignments.items():
        if node_name not in nodes:
            errors.append(f"Assignment references non-existent node: {node_name}")
        ports_seen: dict[int, str] = {}
        for slot in slots:
            model_name = slot.get("model", "")
            if model_name not in models:
                errors.append(f"Assignment on {node_name} references non-existent model: {model_name}")
            port = slot.get("port", 0)
            if port in ports_seen:
                errors.append(f"Duplicate port {port} on node {node_name} (models: {ports_seen[port]}, {model_name})")
            else:
                ports_seen[port] = model_name

    assigned_model_names = {slot.get("model", "") for slots in assignments.values() for slot in slots}
    for model_name in assigned_model_names:
        model = models.get(model_name)
        if model is None:
            continue  # already caught above
        disk_gb = model.get("ram_gb") or model.get("disk_gb", 0)
        if disk_gb and nodes:
            from thunder_forge.cluster.config import OS_OVERHEAD_GB

            fits = any(
                n.get("ram_gb", 0) >= disk_gb + OS_OVERHEAD_GB for n in nodes.values() if n.get("role") == "node"
            )
            if not fits:
                errors.append(
                    f"Model '{model_name}' ({disk_gb} GB) does not fit on any node"
                    f" (after {OS_OVERHEAD_GB} GB OS overhead)"
                )

    try:
        config = parse_cluster_config(config_json)
        mem_errors = validate_memory(config)
        errors.extend(mem_errors)
    except Exception as e:
        errors.append(f"Config parse error: {e}")

    return errors


def save_config_or_error(st, config: dict, user: dict, comment: str) -> bool:
    """Validate and save config with optimistic locking. Shows errors in UI."""
    from thunder_admin import db

    errors = validate_config(config)
    if errors:
        for e in errors:
            st.error(e)
        return False

    loaded_id = st.session_state.get("loaded_config_id")
    new_id = db.save_config(config, user["id"], comment, loaded_id)
    if new_id is None:
        st.error("Config was modified by another user while you were editing. Reload and retry.")
        return False

    st.session_state["loaded_config_id"] = new_id
    return True
