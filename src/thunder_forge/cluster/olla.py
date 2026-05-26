"""Olla router smoke helpers for TF v2 MVP."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

OLLA_OPENAI_PREFIX = "/olla/openai-compatible/v1"
OLLA_DEFAULT_PORT = 40115
OLLA_HEALTH_RETRIES = 30
OLLA_HEALTH_RETRY_INTERVAL = 1.0


@dataclass
class OllaSmokeResult:
    base_url: str
    model: str
    alias: str
    health_ok: bool = False
    endpoints_ok: bool = False
    models_ok: bool = False
    chat_ok: bool = False
    alias_ok: bool = False
    session_ok: bool = False
    root_v1_absent: bool = False
    latency_ms: int = 0
    olla_endpoint: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.health_ok
            and self.endpoints_ok
            and self.models_ok
            and self.chat_ok
            and self.alias_ok
            and self.session_ok
            and self.root_v1_absent
        )


def smoke_olla_router(
    *,
    base_url: str,
    model: str,
    alias: str,
    prompt: str = "Reply with one short word: pong.",
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> OllaSmokeResult:
    """Run a black-box smoke against a running Olla router."""
    normalized_base_url = base_url.rstrip("/")
    result = OllaSmokeResult(base_url=normalized_base_url, model=model, alias=alias)
    fixed_session_id = "tf-olla-smoke-session"

    with httpx.Client(base_url=normalized_base_url, timeout=timeout, transport=transport, trust_env=False) as client:
        try:
            response = client.get("/internal/health")
            result.health_ok = response.is_success
            if not result.health_ok:
                result.errors.append(f"GET /internal/health returned {response.status_code}: {response.text}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /internal/health failed: {exc}")

        try:
            response = client.get("/internal/status/endpoints")
            healthy_msm3 = "msm3-omlx-live" in response.text and "healthy" in response.text.lower()
            result.endpoints_ok = response.is_success and healthy_msm3
            if not result.endpoints_ok:
                result.errors.append(f"GET /internal/status/endpoints unexpected body: {response.text}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /internal/status/endpoints failed: {exc}")

        try:
            response = client.get(f"{OLLA_OPENAI_PREFIX}/models")
            result.models_ok = response.is_success and model in response.text
            if not result.models_ok:
                result.errors.append(f"GET {OLLA_OPENAI_PREFIX}/models missing model '{model}'")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET {OLLA_OPENAI_PREFIX}/models failed: {exc}")

        auth_headers = {"X-Olla-Session-ID": fixed_session_id}
        started = time.perf_counter()
        try:
            response = client.post(
                f"{OLLA_OPENAI_PREFIX}/chat/completions",
                headers=auth_headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16,
                    "temperature": 0,
                    "stream": False,
                },
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.chat_ok = response.is_success
            result.session_ok = response.request.headers.get("X-Olla-Session-ID") == fixed_session_id
            result.olla_endpoint = response.headers.get("X-Olla-Endpoint", "")
            if not result.chat_ok:
                result.errors.append(f"POST {OLLA_OPENAI_PREFIX}/chat/completions returned {response.status_code}")
            if not result.session_ok:
                result.errors.append("X-Olla-Session-ID was not preserved on backend-model request")
        except httpx.HTTPError as exc:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.errors.append(f"POST {OLLA_OPENAI_PREFIX}/chat/completions failed: {exc}")

        try:
            response = client.post(
                f"{OLLA_OPENAI_PREFIX}/chat/completions",
                headers=auth_headers,
                json={
                    "model": alias,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16,
                    "temperature": 0,
                    "stream": False,
                },
            )
            alias_endpoint = response.headers.get("X-Olla-Endpoint", "")
            result.alias_ok = response.is_success and alias_endpoint == result.olla_endpoint
            if not result.alias_ok:
                result.errors.append(f"alias request did not route successfully for '{alias}'")
        except httpx.HTTPError as exc:
            result.errors.append(f"alias POST failed: {exc}")

        try:
            response = client.get("/v1/models")
            result.root_v1_absent = response.status_code == 404
            if not result.root_v1_absent:
                result.errors.append(f"root /v1/models returned {response.status_code}, expected 404 for raw Olla")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /v1/models failed: {exc}")

    return result


@dataclass
class OllaDevSmokeResult:
    """Result of a full dev-smoke: generate config, spawn Olla, smoke, teardown."""

    config_generated: bool = False
    config_path: str = ""
    olla_started: bool = False
    olla_healthy: bool = False
    smoke_result: OllaSmokeResult | None = None
    olla_terminated: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.config_generated
            and self.olla_started
            and self.olla_healthy
            and self.smoke_result is not None
            and self.smoke_result.ok
            and self.olla_terminated
        )


def _generate_olla_config_to_file() -> Path | None:
    """Generate olla-config.yaml from TF desired state. Returns path or None on failure."""
    from thunder_forge.cluster.config import find_repo_root, generate_olla_config, load_cluster_config

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    if not assignments_path.exists():
        return None
    config = load_cluster_config(assignments_path)
    content = generate_olla_config(config)
    config_path = repo_root / "configs" / "olla-config.yaml"
    config_path.write_text(content)
    return config_path


def _wait_olla_healthy(
    base_url: str,
    *,
    retries: int = OLLA_HEALTH_RETRIES,
    interval: float = OLLA_HEALTH_RETRY_INTERVAL,
    timeout: float = 5.0,
) -> bool:
    """Poll Olla /internal/health until it responds or retries are exhausted."""
    for _ in range(retries):
        try:
            with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
                response = client.get("/internal/health")
                if response.is_success:
                    return True
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return False


def dev_smoke_olla(
    *,
    binary: str,
    model: str,
    alias: str,
    prompt: str = "Reply with one short word: pong.",
    smoke_timeout: float = 30.0,
    health_retries: int = OLLA_HEALTH_RETRIES,
    health_interval: float = OLLA_HEALTH_RETRY_INTERVAL,
) -> OllaDevSmokeResult:
    """Generate Olla config, spawn Olla, wait for healthy, smoke, teardown.

    This is the single-command dev-smoke orchestration that replaces manual
    /tmp-based smoke workflows.
    """
    result = OllaDevSmokeResult()

    # Step 1: generate config
    config_path = _generate_olla_config_to_file()
    if config_path is None:
        result.errors.append("Failed to generate olla-config.yaml: node-assignments.yaml not found")
        return result
    result.config_generated = True
    result.config_path = str(config_path)

    # Step 2: spawn Olla
    base_url = f"http://127.0.0.1:{OLLA_DEFAULT_PORT}"
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            [binary, "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result.olla_started = True

        # Step 3: wait for healthy
        result.olla_healthy = _wait_olla_healthy(
            base_url,
            retries=health_retries,
            interval=health_interval,
        )
        if not result.olla_healthy:
            result.errors.append("Olla did not become healthy within retry window")
            return result

        # Step 4: run smoke
        result.smoke_result = smoke_olla_router(
            base_url=base_url,
            model=model,
            alias=alias,
            prompt=prompt,
            timeout=smoke_timeout,
        )

    finally:
        # Step 5: teardown
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            result.olla_terminated = True
        elif proc is not None:
            result.olla_terminated = True

    return result
