"""Shared service port defaults and environment helpers."""

from __future__ import annotations

DEFAULT_EDGE_PORT = 40116
DEFAULT_OLLA_PORT = 40115
DEFAULT_OMLX_PORT = 8018
LOCAL_SERVICE_HOST = "127.0.0.1"


def parse_port(value: int | str, *, name: str) -> int:
    """Parse and validate a TCP port value."""
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be an integer TCP port"
        raise ValueError(msg) from exc
    if port < 1 or port > 65535:
        msg = f"{name} must be between 1 and 65535"
        raise ValueError(msg)
    return port


def resolve_port(value: int | None, *, default: int, name: str = "--port") -> int:
    """Resolve an explicit port or default and validate it."""
    if value is not None:
        return parse_port(value, name=name)
    return parse_port(default, name="default port")


def local_base_url(port: int, *, host: str = LOCAL_SERVICE_HOST) -> str:
    return f"http://{host}:{parse_port(port, name='port')}"
