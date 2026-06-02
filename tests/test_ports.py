"""Tests for shared service port helpers."""

from __future__ import annotations

import pytest

from thunder_forge.cluster.ports import (
    DEFAULT_EDGE_PORT,
    DEFAULT_OLLA_PORT,
    local_base_url,
    parse_port,
    resolve_port,
)


def test_resolve_port_uses_default_when_explicit_value_is_absent() -> None:
    assert resolve_port(None, default=DEFAULT_EDGE_PORT) == DEFAULT_EDGE_PORT
    assert resolve_port(None, default=DEFAULT_OLLA_PORT) == DEFAULT_OLLA_PORT


def test_explicit_port_wins_over_default() -> None:
    assert resolve_port(47000, default=DEFAULT_OLLA_PORT) == 47000


def test_invalid_port_values_raise_clear_errors() -> None:
    with pytest.raises(ValueError, match="default port must be between 1 and 65535"):
        resolve_port(None, default=70000)
    with pytest.raises(ValueError, match="--port must be between 1 and 65535"):
        parse_port(0, name="--port")


def test_local_base_url_uses_validated_port() -> None:
    assert local_base_url(40115) == "http://127.0.0.1:40115"
    assert local_base_url(40115, host="gateway-cache-01.lan") == "http://gateway-cache-01.lan:40115"
