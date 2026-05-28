# Thunder Forge Cluster CLI — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Typer CLI that manages a local MLX inference cluster (4× Mac Studio + 1× Radxa ROCK) through config generation, model distribution, deployment, and health checks.

**Architecture:** Single YAML file (`node-assignments.yaml`) drives everything. CLI commands read it to generate LiteLLM configs, download/sync models, deploy launchd plists via SSH, and check service health. No database, no lock files — fully stateless.

**Tech Stack:** Python 3.12+, Typer, PyYAML, uv, xml.etree.ElementTree, subprocess (SSH/rsync)

**Spec:** `docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md` (copy from vault)
Also in vault: `projects/personal/inference-cluster/specs/2026-03-14-thunder-forge-cluster-cli-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Package metadata, deps, entry point |
| `.gitignore` | Ignore patterns |
| `configs/node-assignments.yaml` | Single source of truth: models, nodes, assignments |
| `configs/litellm-config.yaml` | Auto-generated LiteLLM routing config |
| `docker/docker-compose.yml` | LiteLLM + Open WebUI + PostgreSQL |
| `docker/.env.example` | Template for secrets |
| `src/thunder_forge/__init__.py` | Package marker with version |
| `src/thunder_forge/cli.py` | Typer app, top-level command definitions |
| `src/thunder_forge/cluster/__init__.py` | Cluster subpackage marker |
| `src/thunder_forge/cluster/config.py` | YAML parsing, memory validation, LiteLLM config generation |
| `src/thunder_forge/cluster/health.py` | HTTP health checks for vllm-mlx, Docker service checks |
| `src/thunder_forge/cluster/models.py` | Model download (HF), rsync to nodes |
| `src/thunder_forge/cluster/ssh.py` | Shared SSH/SCP helpers |
| `src/thunder_forge/cluster/deploy.py` | Plist generation, SSH deploy, launchctl, newsyslog |
| `scripts/setup-node.sh` | First-time node bootstrap (inference + infra modes) |
| `.github/workflows/deploy.yml` | GitOps auto-deploy on push to main |
| `tests/test_config.py` | Tests for config parsing, memory validation, config generation |
| `tests/test_health.py` | Tests for health check logic |
| `tests/test_models.py` | Tests for model resolution and sync logic |
| `tests/test_deploy.py` | Tests for plist generation and deploy orchestration |

---

## Chunk 1: Repo Cleanup and Skeleton

### Task 1: Clean the repository

**Files:**
- Delete: everything except `README.md`, `LICENSE`, `.git/`

- [ ] **Step 1: Delete all existing code and configs**

```bash
cd /Users/gnezim/_projects/shared_goals/thunder-forge

# Remove all files/dirs except what we keep
# Keep: README.md, LICENSE, .git/, docs/
rm -rf src/ tests/ scripts/ olla/ .github/ .claude/
rm -f pyproject.toml uv.lock Makefile MANIFEST.in tf.example.yml tf.yml .gitignore
```

- [ ] **Step 2: Verify only README.md, LICENSE, .git/, docs/ remain**

```bash
ls -la
# Expected: .git/  docs/  LICENSE  README.md
```

- [ ] **Step 3: Commit the cleanup**

```bash
git add -A
git commit -m "chore: clean repo for cluster CLI rewrite

Remove Telegram bot, FastAPI app, Ollama monitoring, and all
related code. Keep README.md and LICENSE as foundation for
the new cluster management CLI."
```

---

### Task 2: Create project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/thunder_forge/__init__.py`
- Create: `src/thunder_forge/cluster/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p src/thunder_forge/cluster
mkdir -p tests
mkdir -p configs
mkdir -p docker
mkdir -p scripts
mkdir -p .github/workflows
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "thunder-forge"
version = "0.1.0"
description = "CLI for managing a local MLX inference cluster"
requires-python = ">=3.12"
dependencies = [
    "pyyaml>=6.0",
    "typer>=0.15",
]

[project.scripts]
thunder-forge = "thunder_forge.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.backends"

[tool.hatch.build.targets.wheel]
packages = ["src/thunder_forge"]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.11",
]

[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

- [ ] **Step 3: Write `.gitignore`**

```
# Secrets
docker/.env

# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
dist/
build/
.coverage

# Logs
*.log
*.err

# OS
.DS_Store

# IDE
.idea/
.vscode/
```

- [ ] **Step 4: Write `src/thunder_forge/__init__.py`**

```python
"""Thunder Forge — CLI for managing a local MLX inference cluster."""

__version__ = "0.1.0"
```

- [ ] **Step 5: Write `src/thunder_forge/cluster/__init__.py`**

```python
"""Cluster management commands."""
```

- [ ] **Step 6: Run `uv sync` and verify**

```bash
uv sync
# Expected: resolves dependencies, creates .venv
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src/
git commit -m "feat: add project skeleton with pyproject.toml"
```

---

### Task 3: CLI entry point with stub commands

**Files:**
- Create: `src/thunder_forge/cli.py`

- [ ] **Step 1: Write `cli.py` with all four commands as stubs**

```python
"""Thunder Forge CLI — cluster management commands."""

from typing import Optional

import typer

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)


@app.command()
def generate_config(
    check: bool = typer.Option(False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    typer.echo("generate-config: not implemented yet")
    raise typer.Exit(1)


@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
) -> None:
    """Download and sync models to assigned inference nodes."""
    typer.echo("ensure-models: not implemented yet")
    raise typer.Exit(1)


@app.command()
def deploy(
    node: Optional[str] = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    typer.echo("deploy: not implemented yet")
    raise typer.Exit(1)


@app.command()
def health() -> None:
    """Check health of all cluster services."""
    typer.echo("health: not implemented yet")
    raise typer.Exit(1)
```

- [ ] **Step 2: Verify CLI works**

```bash
uv run thunder-forge --help
# Expected: shows all four commands

uv run thunder-forge generate-config --help
# Expected: shows --check flag
```

- [ ] **Step 3: Commit**

```bash
git add src/thunder_forge/cli.py
git commit -m "feat: add CLI entry point with stub commands"
```

---

### Task 4: Static config files

**Files:**
- Create: `configs/node-assignments.yaml`
- Create: `docker/docker-compose.yml`
- Create: `docker/.env.example`

- [ ] **Step 1: Write `configs/node-assignments.yaml`**

```yaml
# ── Model registry ──────────────────────────────────
# Add new models here. They become available for assignment.
models:
  coder:
    source:
      type: huggingface
      repo: "mlx-community/Qwen3-Coder-Next-4bit"
      revision: "main"
    disk_gb: 44.8
    kv_per_32k_gb: 8
    active_params: "3B of 80B"
    max_context: 131072
    notes: "70%+ SWE-Bench ≈ Sonnet"

# ── Nodes ──────────────────────────────────────────
nodes:
  rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
  msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
  msm2: { ip: "192.168.1.102", ram_gb: 128, user: "admin", role: inference }
  msm3: { ip: "192.168.1.103", ram_gb: 128, user: "admin", role: inference }
  msm4: { ip: "192.168.1.104", ram_gb: 128, user: "admin", role: inference }

# ── Node assignments ────────────────────────────────
# Each node gets a list of {model, port} pairs.
# One vllm-mlx process per entry.
assignments:
  msm1:
    - model: coder
      port: 8000
  msm2:
    - model: coder
      port: 8000
  msm3:
    - model: coder
      port: 8000
  msm4:
    - model: coder
      port: 8000
```

- [ ] **Step 2: Write `docker/docker-compose.yml`**

```yaml
name: thunder-forge

services:
  postgres:
    image: postgres:16-alpine
    container_name: postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: litellm
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-litellm-local}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U litellm"]
      interval: 10s
      timeout: 3s
      retries: 3
    networks: [infra]

  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: litellm
    restart: unless-stopped
    ports:
      - "4000:4000"
    volumes:
      - ../configs/litellm-config.yaml:/app/config.yaml:ro
    command:
      - "--config"
      - "/app/config.yaml"
      - "--port"
      - "4000"
      - "--host"
      - "0.0.0.0"
      - "--num_workers"
      - "4"
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY:-sk-local-cluster-key}
      DATABASE_URL: postgresql://litellm:${POSTGRES_PASSWORD:-litellm-local}@postgres:5432/litellm
      UI_USERNAME: ${UI_USERNAME:-admin}
      UI_PASSWORD: ${UI_PASSWORD:-changeme}
    depends_on:
      postgres: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c",
        "import urllib.request; urllib.request.urlopen('http://localhost:4000/health/readiness')"]
      interval: 15s
      timeout: 5s
      retries: 3
    networks: [infra]

  openwebui:
    image: ghcr.io/open-webui/open-webui:latest
    container_name: openwebui
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - openwebui-data:/app/backend/data
    environment:
      OPENAI_API_BASE_URLS: http://litellm:4000/v1
      OPENAI_API_KEYS: ${LITELLM_MASTER_KEY:-sk-local-cluster-key}
      ENABLE_OLLAMA_API: "false"
      WEBUI_AUTH: ${WEBUI_AUTH:-true}
      WEBUI_SECRET_KEY: ${WEBUI_SECRET_KEY:-change-this-secret}
      WEBUI_NAME: LLM Cluster
      ENABLE_SIGNUP: ${ENABLE_SIGNUP:-true}
    depends_on:
      litellm: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8080/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    networks: [infra]

networks:
  infra:
    driver: bridge

volumes:
  postgres-data:
  openwebui-data:
```

- [ ] **Step 3: Write `docker/.env.example`**

```bash
LITELLM_MASTER_KEY=sk-local-cluster-key
POSTGRES_PASSWORD=litellm-local
UI_USERNAME=admin
UI_PASSWORD=changeme
WEBUI_SECRET_KEY=change-this-secret
WEBUI_AUTH=true
ENABLE_SIGNUP=true
```

- [ ] **Step 4: Commit**

```bash
git add configs/ docker/
git commit -m "feat: add node-assignments.yaml and docker-compose stack"
```

---

## Chunk 2: `generate-config` Command

### Task 5: YAML parser and data models

**Files:**
- Create: `src/thunder_forge/cluster/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test for YAML parsing**

`tests/test_config.py`:

```python
"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config


@pytest.fixture()
def assignments_yaml(tmp_path: Path) -> Path:
    """Create a minimal node-assignments.yaml for testing."""
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
              revision: "main"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_load_cluster_config(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].ip == "192.168.1.101"
    assert config.nodes["msm1"].role == "inference"
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "infra"
    assert len(config.assignments["msm1"]) == 1
    assert config.assignments["msm1"][0].model == "coder"
    assert config.assignments["msm1"][0].port == 8000
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py::test_load_cluster_config -v
# Expected: FAIL — ImportError (module doesn't exist yet)
```

- [ ] **Step 3: Implement `config.py` with data classes and YAML loader**

`src/thunder_forge/cluster/config.py`:

```python
"""Config parsing, memory validation, and LiteLLM config generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
class Model:
    source: ModelSource
    disk_gb: float = 0.0
    kv_per_32k_gb: float = 0.0
    ram_gb: float | None = None
    active_params: str = ""
    max_context: int = 0
    serving: str = ""
    notes: str = ""


@dataclass
class Node:
    ip: str
    ram_gb: int
    user: str = "admin"
    role: str = "inference"


@dataclass
class Assignment:
    model: str
    port: int = 0  # 0 for non-LLM models (cli, pip without server)
    embedding: bool = False


@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    assignments: dict[str, list[Assignment]] = field(default_factory=dict)

    @property
    def inference_nodes(self) -> dict[str, Node]:
        """Return only inference nodes (exclude infra)."""
        return {k: v for k, v in self.nodes.items() if v.role == "inference"}

    @property
    def rock(self) -> Node:
        """Return the infra node."""
        for node in self.nodes.values():
            if node.role == "infra":
                return node
        msg = "No infra node found in config"
        raise ValueError(msg)


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


def _parse_model(raw: dict) -> Model:
    return Model(
        source=_parse_model_source(raw["source"]),
        disk_gb=raw.get("disk_gb", 0.0),
        kv_per_32k_gb=raw.get("kv_per_32k_gb", 0.0),
        ram_gb=raw.get("ram_gb"),
        active_params=raw.get("active_params", ""),
        max_context=raw.get("max_context", 0),
        serving=raw.get("serving", ""),
        notes=raw.get("notes", ""),
    )


def load_cluster_config(path: Path) -> ClusterConfig:
    """Load and parse node-assignments.yaml into a ClusterConfig."""
    with path.open() as f:
        raw = yaml.safe_load(f)

    models = {k: _parse_model(v) for k, v in raw.get("models", {}).items()}

    nodes = {}
    for k, v in raw.get("nodes", {}).items():
        nodes[k] = Node(
            ip=v["ip"],
            ram_gb=v["ram_gb"],
            user=v.get("user", "admin"),
            role=v.get("role", "inference"),
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

    return ClusterConfig(models=models, nodes=nodes, assignments=assignments)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_config.py::test_load_cluster_config -v
# Expected: PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/test_config.py
git commit -m "feat: add YAML parser for node-assignments"
```

---

### Task 6: Memory validator

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for memory validation**

Add to `tests/test_config.py`:

```python
from thunder_forge.cluster.config import validate_memory


def test_validate_memory_single_model_passes(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    errors = validate_memory(config)
    assert errors == []


@pytest.fixture()
def overloaded_yaml(tmp_path: Path) -> Path:
    """Config where a node exceeds its RAM budget."""
    content = dedent("""\
        models:
          big_model:
            source: { type: huggingface, repo: "test/big" }
            disk_gb: 100
            kv_per_32k_gb: 30
            max_context: 32768

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }

        assignments:
          msm1:
            - model: big_model
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_overloaded_fails(overloaded_yaml: Path) -> None:
    config = load_cluster_config(overloaded_yaml)
    errors = validate_memory(config)
    assert len(errors) == 1
    assert "msm1" in errors[0]


@pytest.fixture()
def multi_model_yaml(tmp_path: Path) -> Path:
    """Config with two models on one node, fits in budget."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          general:
            source: { type: huggingface, repo: "test/general" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }

        assignments:
          msm1:
            - model: coder
              port: 8000
            - model: general
              port: 8001
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_multi_model_passes(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    errors = validate_memory(config)
    # 44.8+8 + 44.8+8 + 8 OS = 113.6 <= 128 ✅
    assert errors == []


def test_validate_memory_uses_ram_gb_override(tmp_path: Path) -> None:
    """Models with ram_gb should use that instead of disk_gb for budget."""
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 120
            max_context: 0
            serving: cli

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    errors = validate_memory(config)
    # 120 + 0 kv + 8 OS = 128 <= 128 ✅ (barely fits)
    assert errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -k "validate_memory" -v
# Expected: FAIL — validate_memory not defined
```

- [ ] **Step 3: Implement `validate_memory` in `config.py`**

Add to `src/thunder_forge/cluster/config.py`:

```python
OS_OVERHEAD_GB = 8


def validate_memory(config: ClusterConfig) -> list[str]:
    """Validate that assigned models fit within each node's RAM.

    Returns a list of error strings (empty = all valid).
    """
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
        else:
            # Print info even on success (for CLI output)
            pass

    return errors
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -k "validate_memory" -v
# Expected: all 4 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/test_config.py
git commit -m "feat: add memory budget validator"
```

---

### Task 7: LiteLLM config generator

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for config generation**

Add to `tests/test_config.py`:

```python
from thunder_forge.cluster.config import generate_litellm_config


def test_generate_litellm_config_basic(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    result = generate_litellm_config(config)

    # Should be valid YAML
    parsed = yaml.safe_load(result)

    # Header comment
    assert result.startswith("# AUTO-GENERATED")

    # Model list
    assert len(parsed["model_list"]) == 1
    entry = parsed["model_list"][0]
    assert entry["model_name"] == "coder"
    assert entry["litellm_params"]["model"] == "hosted_vllm/mlx-community/Qwen3-Coder-Next-4bit"
    assert entry["litellm_params"]["api_base"] == "http://192.168.1.101:8000/v1"
    assert entry["litellm_params"]["api_key"] == "none"
    assert entry["litellm_params"]["max_input_tokens"] == 131072
    assert entry["litellm_params"]["max_output_tokens"] == 16384

    # Settings
    assert parsed["litellm_settings"]["callbacks"] == ["prometheus"]
    assert parsed["router_settings"]["routing_strategy"] == "least-busy"
    assert parsed["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_generate_litellm_config_multi_node(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    result = generate_litellm_config(config)
    parsed = yaml.safe_load(result)

    # msm1 has coder:8000 + general:8001 = 2 entries
    assert len(parsed["model_list"]) == 2
    names = {e["model_name"] for e in parsed["model_list"]}
    assert names == {"coder", "general"}


def test_generate_litellm_config_embedding_slot(tmp_path: Path) -> None:
    """When a slot has embedding: true, an extra embedding entry should be generated."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          embedding:
            source: { type: huggingface, repo: "test/embedding-model" }
            disk_gb: 0.5
            serving: embedding

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }

        assignments:
          msm1:
            - model: coder
              port: 8000
              embedding: true
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml.safe_load(result)
    # Should have coder entry + embedding entry = 2
    assert len(parsed["model_list"]) == 2
    names = [e["model_name"] for e in parsed["model_list"]]
    assert "coder" in names
    assert "embedding" in names
    emb_entry = next(e for e in parsed["model_list"] if e["model_name"] == "embedding")
    assert emb_entry["litellm_params"]["model"] == "openai/test/embedding-model"
    assert emb_entry["litellm_params"]["api_base"] == "http://192.168.1.101:8000/v1"


def test_generate_litellm_config_skips_cli_serving(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 20
            max_context: 0
            serving: cli

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml.safe_load(result)
    # CLI-only models should not appear in model_list
    assert len(parsed["model_list"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -k "generate_litellm" -v
# Expected: FAIL — generate_litellm_config not defined
```

- [ ] **Step 3: Implement `generate_litellm_config` in `config.py`**

Add to `src/thunder_forge/cluster/config.py`:

```python
def generate_litellm_config(config: ClusterConfig) -> str:
    """Generate litellm-config.yaml content from cluster config.

    Returns the YAML string with a header comment.
    """
    model_list: list[dict] = []

    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]

        for slot in slots:
            model = config.models[slot.model]

            # Skip models that don't get a LiteLLM entry
            if model.serving in ("embedding", "cli"):
                continue

            # Determine provider prefix
            if model.serving == "mlx-openai-server":
                provider = "openai"
            else:
                provider = "hosted_vllm"

            entry: dict = {
                "model_name": slot.model,
                "litellm_params": {
                    "model": f"{provider}/{model.source.repo}",
                    "api_base": f"http://{node.ip}:{slot.port}/v1",
                    "api_key": "none",
                },
            }

            if model.max_context > 0:
                entry["litellm_params"]["max_input_tokens"] = model.max_context
                entry["litellm_params"]["max_output_tokens"] = 16384

            model_list.append(entry)

            # If embedding is enabled on this slot, add embedding entry
            if slot.embedding:
                emb_model = config.models.get("embedding")
                if emb_model:
                    model_list.append({
                        "model_name": "embedding",
                        "litellm_params": {
                            "model": f"openai/{emb_model.source.repo}",
                            "api_base": f"http://{node.ip}:{slot.port}/v1",
                            "api_key": "none",
                        },
                    })

    output: dict = {
        "model_list": model_list,
        "litellm_settings": {
            "num_retries": 2,
            "timeout": 120,
            "allowed_fails": 3,
            "cooldown_time": 30,
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -k "generate_litellm" -v
# Expected: all 3 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/test_config.py
git commit -m "feat: add LiteLLM config generator"
```

---

### Task 8: `--check` mode and repo root detection

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for `--check` mode**

Add to `tests/test_config.py`:

```python
from thunder_forge.cluster.config import check_config_sync


def test_check_config_sync_matches(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    generated = generate_litellm_config(config)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text(generated)
    assert check_config_sync(config, committed) is True


def test_check_config_sync_mismatch(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text("stale content")
    assert check_config_sync(config, committed) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -k "check_config" -v
# Expected: FAIL — check_config_sync not defined
```

- [ ] **Step 3: Implement `check_config_sync` and `find_repo_root`**

Add to `src/thunder_forge/cluster/config.py`:

```python
import subprocess


def find_repo_root() -> Path:
    """Find the repository root directory.

    Tries git first, then walks parents looking for configs/node-assignments.yaml.
    """
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
    """Check if the generated config matches the committed file.

    Returns True if they match, False otherwise.
    """
    generated = generate_litellm_config(config)

    if not committed_path.exists():
        return False

    committed = committed_path.read_text()
    return generated == committed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -k "check_config" -v
# Expected: all 2 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/test_config.py
git commit -m "feat: add --check mode and repo root detection"
```

---

### Task 9: Wire `generate-config` command in CLI

**Files:**
- Modify: `src/thunder_forge/cli.py`

- [ ] **Step 1: Replace the stub `generate_config` command**

Update `src/thunder_forge/cli.py` `generate_config` function:

```python
@app.command()
def generate_config(
    check: bool = typer.Option(False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import (
        check_config_sync,
        find_repo_root,
        generate_litellm_config,
        load_cluster_config,
        validate_memory,
    )

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config_path = repo_root / "configs" / "litellm-config.yaml"

    if not assignments_path.exists():
        typer.echo(f"Error: {assignments_path} not found", err=True)
        raise typer.Exit(1)

    config = load_cluster_config(assignments_path)

    # Memory validation
    typer.echo("Validating memory budgets...")
    errors = validate_memory(config)
    # Print per-node breakdown
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        parts = []
        total = 8  # OS overhead
        for slot in slots:
            model = config.models[slot.model]
            weight = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv = model.kv_per_32k_gb
            total += weight + kv
            parts.append(f"{slot.model}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + 8 OS = {total:.1f} GB / {node.ram_gb} GB"
        status = "✅" if total <= node.ram_gb else "❌ EXCEEDS"
        typer.echo(f"  {node_name}: {budget} {status}")

    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    if check:
        if check_config_sync(config, config_path):
            typer.echo("✅ Config is in sync with assignments")
            raise typer.Exit(0)
        else:
            typer.echo("❌ Config mismatch — run 'thunder-forge generate-config' to update", err=True)
            raise typer.Exit(1)

    # Generate and write
    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"✅ Generated {config_path}")
```

- [ ] **Step 2: Test the full flow manually**

```bash
cd /Users/gnezim/_projects/shared_goals/thunder-forge
uv run thunder-forge generate-config
# Expected:
# Validating memory budgets...
#   msm1: coder(44.8+8kv) + 8 OS = 60.8 GB / 128 GB ✅
#   ...
# ✅ Generated .../configs/litellm-config.yaml

cat configs/litellm-config.yaml
# Expected: valid YAML with model_list entries
```

- [ ] **Step 3: Run all config tests**

```bash
uv run pytest tests/test_config.py -v
# Expected: all PASS
```

- [ ] **Step 4: Commit**

```bash
git add src/thunder_forge/cli.py configs/litellm-config.yaml
git commit -m "feat: wire generate-config command with memory validation"
```

---

## Chunk 3: `health` Command

### Task 10: Health check implementation

**Files:**
- Create: `src/thunder_forge/cluster/health.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Write failing test for inference health check logic**

`tests/test_health.py`:

```python
"""Tests for health check logic."""

from unittest.mock import patch, MagicMock
from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config
from thunder_forge.cluster.health import check_inference_node, check_docker_services


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


@patch("thunder_forge.cluster.health.urllib.request.urlopen")
def test_check_inference_node_healthy(mock_urlopen: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    result = check_inference_node("192.168.1.101", 8000)
    assert result is True
    mock_urlopen.assert_called_once()


@patch("thunder_forge.cluster.health.urllib.request.urlopen")
def test_check_inference_node_unreachable(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = Exception("Connection refused")

    result = check_inference_node("192.168.1.101", 8000)
    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_health.py -v
# Expected: FAIL — ImportError
```

- [ ] **Step 3: Implement `health.py`**

`src/thunder_forge/cluster/health.py`:

```python
"""Health checks for inference nodes and infrastructure services."""

from __future__ import annotations

import json
import subprocess
import urllib.request
import urllib.error

from thunder_forge.cluster.config import ClusterConfig


def check_inference_node(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if a vllm-mlx endpoint is healthy (model loaded and serving).

    Returns True if HTTP 200 from /v1/models, False otherwise.
    """
    url = f"http://{ip}:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def check_docker_services(
    rock_ip: str,
    rock_user: str,
    expected_services: tuple[str, ...] = ("litellm", "openwebui", "postgres"),
) -> dict[str, bool]:
    """Check Docker Compose service health on rock via SSH.

    Returns a dict of {service_name: is_healthy}.
    """
    results = {svc: False for svc in expected_services}

    try:
        cmd = [
            "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            f"{rock_user}@{rock_ip}",
            "cd ~/thunder-forge/docker && docker compose ps --format json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if proc.returncode != 0:
            return results

        # docker compose ps --format json outputs one JSON object per line
        for line in proc.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                svc = json.loads(line)
                name = svc.get("Name", svc.get("Service", ""))
                state = svc.get("State", "")
                health = svc.get("Health", "")
                for expected in expected_services:
                    if expected in name:
                        results[expected] = state == "running" and health in ("healthy", "")
            except json.JSONDecodeError:
                continue

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return results


def run_health_checks(config: ClusterConfig) -> bool:
    """Run all health checks and print results.

    Returns True if all checks pass, False otherwise.
    """
    all_healthy = True

    # Inference nodes
    print("=== Inference ===")
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            healthy = check_inference_node(node.ip, slot.port)
            status = "✅" if healthy else "❌"
            print(f"  {node_name}:{slot.port} ({slot.model}): {status}")
            if not healthy:
                all_healthy = False

    # Infrastructure
    print("\n=== Infrastructure ===")
    rock = config.rock
    docker_health = check_docker_services(rock.ip, rock.user)
    display_names = {"litellm": "LiteLLM", "openwebui": "Open WebUI", "postgres": "PostgreSQL"}
    for svc, healthy in docker_health.items():
        status = "✅" if healthy else "❌"
        name = display_names.get(svc, svc)
        print(f"  {name:12s} {status}")
        if not healthy:
            all_healthy = False

    # Assignments summary
    print("\n=== Model Assignments ===")
    for node_name, slots in sorted(config.assignments.items()):
        slot_strs = [f"{s.model}:{s.port}" for s in slots]
        print(f"  {node_name}: {', '.join(slot_strs)}")

    return all_healthy
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_health.py -v
# Expected: all PASS
```

- [ ] **Step 5: Wire the `health` command in CLI**

Update `src/thunder_forge/cli.py` `health` function:

```python
@app.command()
def health() -> None:
    """Check health of all cluster services."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config
    from thunder_forge.cluster.health import run_health_checks

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config = load_cluster_config(assignments_path)

    all_healthy = run_health_checks(config)
    raise typer.Exit(0 if all_healthy else 1)
```

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/health.py tests/test_health.py src/thunder_forge/cli.py
git commit -m "feat: add health command with inference and docker checks"
```

---

## Chunk 4: SSH Utilities and `ensure-models` Command

### Task 11a: Shared SSH helpers

**Files:**
- Create: `src/thunder_forge/cluster/ssh.py`

- [ ] **Step 1: Write `ssh.py`**

`src/thunder_forge/cluster/ssh.py`:

```python
"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import subprocess


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{user}@{ip}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def run_local(
    cmd: list[str],
    *,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command locally."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scp_content(
    user: str,
    ip: str,
    content: str,
    remote_path: str,
) -> subprocess.CompletedProcess[str]:
    """Write content to a remote file via SSH stdin pipe."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{user}@{ip}", f"cat > {remote_path}"],
        input=content, capture_output=True, text=True, timeout=15,
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/thunder_forge/cluster/ssh.py
git commit -m "feat: add shared SSH/SCP helpers"
```

---

### Task 11b: Model download and sync

**Files:**
- Create: `src/thunder_forge/cluster/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test for model resolution logic**

`tests/test_models.py`:

```python
"""Tests for model download and sync logic."""

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch, MagicMock

import pytest

from thunder_forge.cluster.config import load_cluster_config
from thunder_forge.cluster.models import resolve_model_tasks


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
              revision: "main"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          local_model:
            source:
              type: local
              path: "/Users/admin/models/custom"
            disk_gb: 30
            kv_per_32k_gb: 6
            max_context: 32768

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          msm2: { ip: "192.168.1.102", ram_gb: 128, user: "admin", role: inference }

        assignments:
          msm1:
            - model: coder
              port: 8000
          msm2:
            - model: coder
              port: 8000
            - model: local_model
              port: 8001
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_resolve_model_tasks(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    tasks = resolve_model_tasks(config)

    # coder is assigned to msm1 and msm2 — one download, two syncs
    coder_tasks = [t for t in tasks if t.model_name == "coder"]
    assert len(coder_tasks) == 1
    assert set(coder_tasks[0].target_nodes) == {"msm1", "msm2"}
    assert coder_tasks[0].source_type == "huggingface"

    # local_model is assigned to msm2 — just verify, no download
    local_tasks = [t for t in tasks if t.model_name == "local_model"]
    assert len(local_tasks) == 1
    assert local_tasks[0].source_type == "local"
    assert local_tasks[0].target_nodes == ["msm2"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_models.py -v
# Expected: FAIL — ImportError
```

- [ ] **Step 3: Implement `models.py`**

`src/thunder_forge/cluster/models.py`:

```python
"""Model download and sync to inference nodes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ssh import ssh_run, run_local


@dataclass
class ModelTask:
    """A model that needs to be downloaded and/or synced to nodes."""
    model_name: str
    source_type: str  # huggingface, convert, local, pip
    repo: str = ""
    revision: str = "main"
    quantize: str = ""
    path: str = ""
    package: str = ""
    weight_repo: str = ""
    target_nodes: list[str] = field(default_factory=list)


def resolve_model_tasks(
    config: ClusterConfig,
    *,
    target_node: str | None = None,
) -> list[ModelTask]:
    """Resolve which models need to be downloaded/synced to which nodes.

    Deduplicates: each model appears once with all target nodes listed.
    If target_node is set, only include assignments for that node.
    """
    task_map: dict[str, ModelTask] = {}

    for node_name, slots in config.assignments.items():
        if target_node and node_name != target_node:
            continue

        for slot in slots:
            model = config.models[slot.model]
            src = model.source

            if slot.model not in task_map:
                task_map[slot.model] = ModelTask(
                    model_name=slot.model,
                    source_type=src.type,
                    repo=src.repo,
                    revision=src.revision,
                    quantize=src.quantize,
                    path=src.path,
                    package=src.package,
                    weight_repo=src.weight_repo,
                )

            task_map[slot.model].target_nodes.append(node_name)

    return list(task_map.values())


def _check_hf_cached(user: str, ip: str, repo: str) -> bool:
    """Check if a HuggingFace model snapshot exists on a remote node."""
    hf_path = repo.replace("/", "--")
    result = ssh_run(user, ip, f"test -d ~/.cache/huggingface/hub/models--{hf_path}/snapshots")
    return result.returncode == 0


def ensure_huggingface(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    """Download a HuggingFace model on rock, rsync to target nodes.

    Returns list of error strings (empty = success).
    """
    errors: list[str] = []
    rock = config.rock

    if dry_run:
        print(f"  [dry-run] Would download {task.repo} (rev: {task.revision}) on rock")
        for node_name in task.target_nodes:
            print(f"  [dry-run] Would rsync to {node_name}")
        return errors

    # Download on rock (idempotent — huggingface-cli skips existing)
    print(f"  Downloading {task.repo} on rock...")
    dl_cmd = f"huggingface-cli download {task.repo} --revision {task.revision}"
    result = ssh_run(rock.user, rock.ip, dl_cmd, timeout=600)
    if result.returncode != 0:
        errors.append(f"Download failed for {task.repo}: {result.stderr.strip()}")
        return errors  # Abort before syncing

    # Rsync to each target node (skip if snapshot already exists)
    hf_cache_path = task.repo.replace("/", "--")
    src_path = f"{rock.user}@{rock.ip}:~/.cache/huggingface/hub/models--{hf_cache_path}/"

    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if _check_hf_cached(node.user, node.ip, task.repo):
            print(f"  ✅ {task.model_name} already cached on {node_name}")
            continue

        dest_path = f"{node.user}@{node.ip}:~/.cache/huggingface/hub/models--{hf_cache_path}/"
        print(f"  Syncing {task.model_name} to {node_name}...")

        rsync_result = run_local(
            ["rsync", "-az", "--progress", "-e", "ssh -o StrictHostKeyChecking=no",
             src_path, dest_path],
            timeout=600,
        )
        if rsync_result.returncode != 0:
            errors.append(f"Rsync to {node_name} failed: {rsync_result.stderr.strip()}")

    return errors


def ensure_convert(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    """Download source model on rock, convert on an inference node, rsync to targets.

    Returns list of error strings (empty = success).
    """
    errors: list[str] = []
    rock = config.rock

    if dry_run:
        print(f"  [dry-run] Would download {task.repo}, convert (q={task.quantize}), sync to {task.target_nodes}")
        return errors

    # Download source on rock
    print(f"  Downloading source {task.repo} on rock...")
    dl_result = ssh_run(rock.user, rock.ip, f"huggingface-cli download {task.repo}", timeout=600)
    if dl_result.returncode != 0:
        errors.append(f"Download failed for {task.repo}: {dl_result.stderr.strip()}")
        return errors

    # Pick first inference node for conversion (MLX requires macOS)
    convert_node_name = task.target_nodes[0]
    convert_node = config.nodes[convert_node_name]
    output_dir = f"~/.cache/mlx-models/{task.model_name}/"

    # Check if already converted
    check = ssh_run(convert_node.user, convert_node.ip, f"test -d {output_dir}")
    if check.returncode == 0:
        print(f"  ✅ Already converted on {convert_node_name}")
    else:
        print(f"  Converting on {convert_node_name} (quantize={task.quantize})...")
        convert_cmd = (
            f"python -m mlx_lm.convert --hf-path {task.repo} "
            f"-q --q-bits {task.quantize} --upload-repo '' --mlx-path {output_dir}"
        )
        conv_result = ssh_run(convert_node.user, convert_node.ip, convert_cmd, timeout=1800)
        if conv_result.returncode != 0:
            errors.append(f"Conversion failed on {convert_node_name}: {conv_result.stderr.strip()}")
            return errors

    # Rsync converted weights to remaining target nodes
    for node_name in task.target_nodes[1:]:
        node = config.nodes[node_name]
        check = ssh_run(node.user, node.ip, f"test -d {output_dir}")
        if check.returncode == 0:
            print(f"  ✅ Already on {node_name}")
            continue

        print(f"  Syncing converted model to {node_name}...")
        src = f"{convert_node.user}@{convert_node.ip}:{output_dir}"
        dest = f"{node.user}@{node.ip}:{output_dir}"
        rsync_result = run_local(
            ["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", src, dest],
            timeout=600,
        )
        if rsync_result.returncode != 0:
            errors.append(f"Rsync to {node_name} failed: {rsync_result.stderr.strip()}")

    return errors


def ensure_local(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    """Verify a local model path exists on target nodes."""
    errors: list[str] = []
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"  [dry-run] Would verify {task.path} exists on {node_name}")
            continue
        result = ssh_run(node.user, node.ip, f"test -d {task.path}")
        if result.returncode != 0:
            errors.append(f"{node_name}: path {task.path} does not exist")
    return errors


def ensure_pip(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    """Install a pip package on target nodes."""
    errors: list[str] = []
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"  [dry-run] Would install {task.package} on {node_name}")
            continue

        # Check if already installed
        check = ssh_run(node.user, node.ip, f"uv tool list 2>/dev/null | grep -q {task.package}")
        if check.returncode == 0:
            print(f"  ✅ {task.package} already installed on {node_name}")
        else:
            print(f"  Installing {task.package} on {node_name}...")
            result = ssh_run(node.user, node.ip, f"uv tool install {task.package}", timeout=120)
            if result.returncode != 0:
                errors.append(f"{node_name}: install of {task.package} failed: {result.stderr.strip()}")

        if task.weight_repo:
            print(f"  Pre-downloading weights {task.weight_repo} on {node_name}...")
            dl_result = ssh_run(node.user, node.ip, f"huggingface-cli download {task.weight_repo}", timeout=600)
            if dl_result.returncode != 0:
                errors.append(f"{node_name}: weight download failed: {dl_result.stderr.strip()}")
    return errors


def run_ensure_models(
    config: ClusterConfig,
    *,
    dry_run: bool = False,
    target_node: str | None = None,
) -> bool:
    """Ensure all assigned models are present on their target nodes.

    Returns True if all succeeded, False if any errors.
    """
    tasks = resolve_model_tasks(config, target_node=target_node)
    all_ok = True

    for task in tasks:
        print(f"\n📦 {task.model_name} ({task.source_type})")

        handler = {
            "huggingface": ensure_huggingface,
            "convert": ensure_convert,
            "local": ensure_local,
            "pip": ensure_pip,
        }.get(task.source_type)

        if handler is None:
            print(f"  ⚠️  Source type '{task.source_type}' not yet implemented (skipping)")
            continue

        errors = handler(task, config, dry_run=dry_run)
        if errors:
            all_ok = False
            for err in errors:
                print(f"  ❌ {err}")
        elif not dry_run:
            print(f"  ✅ Done")

    return all_ok
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -v
# Expected: all PASS
```

- [ ] **Step 5: Wire the `ensure-models` command in CLI**

Update `src/thunder_forge/cli.py` `ensure_models` function:

```python
@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
) -> None:
    """Download and sync models to assigned inference nodes."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config
    from thunder_forge.cluster.models import run_ensure_models

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config = load_cluster_config(assignments_path)

    success = run_ensure_models(config, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)
```

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/models.py tests/test_models.py src/thunder_forge/cli.py
git commit -m "feat: add ensure-models command for model download and sync"
```

---

## Chunk 5: `deploy` Command

### Task 12: Plist generation

**Files:**
- Create: `src/thunder_forge/cluster/deploy.py`
- Create: `tests/test_deploy.py`

- [ ] **Step 1: Write failing test for plist generation**

`tests/test_deploy.py`:

```python
"""Tests for deploy logic: plist generation, orchestration."""

import xml.etree.ElementTree as ET
from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config
from thunder_forge.cluster.deploy import generate_plist


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
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_generate_plist_basic(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]

    xml_str = generate_plist(model, slot, node)

    # Should be valid XML
    root = ET.fromstring(xml_str)
    assert root.tag == "plist"

    # Find Label
    dict_elem = root.find("dict")
    keys = [k.text for k in dict_elem.findall("key")]
    assert "Label" in keys
    assert "ProgramArguments" in keys
    assert "RunAtLoad" in keys
    assert "KeepAlive" in keys
    assert "ProcessType" in keys

    # Check Label value
    label_idx = keys.index("Label")
    values = list(dict_elem)
    label_val = values[label_idx * 2 + 1].text  # key, value pairs
    # More robust: parse as plist
    assert "com.vllm-mlx-8000" in xml_str

    # Check program arguments contain the model repo and port
    assert "mlx-community/Qwen3-Coder-Next-4bit" in xml_str
    assert "--port" in xml_str
    assert "8000" in xml_str
    assert "--continuous-batching" in xml_str
    assert "--max-model-len" in xml_str
    assert "131072" in xml_str
    assert "Interactive" in xml_str


def test_generate_plist_with_embedding(config_path: Path) -> None:
    config = load_cluster_config(config_path)

    # Add embedding model to registry
    from thunder_forge.cluster.config import Model, ModelSource, Assignment
    config.models["embedding"] = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"),
        disk_gb=0.5,
        serving="embedding",
    )

    model = config.models["coder"]
    slot = Assignment(model="coder", port=8000, embedding=True)
    node = config.nodes["msm1"]

    xml_str = generate_plist(model, slot, node, embedding_model=config.models.get("embedding"))
    assert "--embedding-model" in xml_str
    assert "Qwen3-Embedding-0.6B-4bit-DWQ" in xml_str
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_deploy.py -v
# Expected: FAIL — ImportError
```

- [ ] **Step 3: Implement plist generation in `deploy.py`**

`src/thunder_forge/cluster/deploy.py`:

```python
"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node
from thunder_forge.cluster.ssh import ssh_run, scp_content


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
    *,
    embedding_model: Model | None = None,
) -> str:
    """Generate a launchd plist XML string for a vllm-mlx service."""
    label = f"com.vllm-mlx-{slot.port}"
    user_home = f"/Users/{node.user}"
    vllm_path = f"{user_home}/.local/bin/vllm-mlx"

    program_args = [
        vllm_path,
        "serve",
        model.source.repo,
        "--port", str(slot.port),
        "--host", "0.0.0.0",
        "--continuous-batching",
        "--max-model-len", str(model.max_context),
    ]

    if slot.embedding and embedding_model:
        program_args.extend(["--embedding-model", embedding_model.source.repo])

    env_vars = {
        "PATH": f"{user_home}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": user_home,
    }

    # Build plist XML using ElementTree
    plist = ET.Element("plist", version="1.0")
    d = ET.SubElement(plist, "dict")

    def add_key_value(parent: ET.Element, key: str, value_elem: ET.Element) -> None:
        k = ET.SubElement(parent, "key")
        k.text = key
        parent.append(value_elem)

    def make_string(text: str) -> ET.Element:
        e = ET.Element("string")
        e.text = text
        return e

    def make_true() -> ET.Element:
        return ET.Element("true")

    def make_integer(val: int) -> ET.Element:
        e = ET.Element("integer")
        e.text = str(val)
        return e

    # Label
    add_key_value(d, "Label", make_string(label))

    # ProgramArguments
    k = ET.SubElement(d, "key")
    k.text = "ProgramArguments"
    arr = ET.SubElement(d, "array")
    for arg in program_args:
        s = ET.SubElement(arr, "string")
        s.text = arg

    # EnvironmentVariables
    k = ET.SubElement(d, "key")
    k.text = "EnvironmentVariables"
    env_dict = ET.SubElement(d, "dict")
    for env_key, env_val in env_vars.items():
        ek = ET.SubElement(env_dict, "key")
        ek.text = env_key
        ev = ET.SubElement(env_dict, "string")
        ev.text = env_val

    # Log paths
    add_key_value(d, "StandardOutPath", make_string(f"{user_home}/logs/vllm-mlx-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{user_home}/logs/vllm-mlx-{slot.port}.err"))

    # Service behavior
    add_key_value(d, "RunAtLoad", make_true())
    add_key_value(d, "KeepAlive", make_true())
    add_key_value(d, "ThrottleInterval", make_integer(10))
    add_key_value(d, "ProcessType", make_string("Interactive"))

    # Render XML
    ET.indent(plist, space="  ")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    doctype = '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    body = ET.tostring(plist, encoding="unicode")
    return xml_declaration + doctype + body + "\n"


NEWSYSLOG_CONF = """\
# logfilename                             [owner:group] mode count size(KB) when  flags
/Users/{user}/logs/vllm-mlx-*.log            {user}:staff     644  7     102400   *     CNJ
/Users/{user}/logs/vllm-mlx-*.err            {user}:staff     644  7     102400   *     CNJ
"""


def deploy_node(
    node_name: str,
    config: ClusterConfig,
) -> list[str]:
    """Deploy vllm-mlx plists and newsyslog config to a single node.

    Returns list of error strings (empty = success).
    """
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])

    if not slots:
        return [f"{node_name}: no assignments found"]

    # Ensure ~/logs exists
    ssh_run(node.user, node.ip, "mkdir -p ~/logs")

    # Ensure ~/Library/LaunchAgents exists
    ssh_run(node.user, node.ip, "mkdir -p ~/Library/LaunchAgents")

    # Get UID for launchctl
    uid_result = ssh_run(node.user, node.ip, "id -u")
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID"]
    uid = uid_result.stdout.strip()

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        embedding_model = config.models.get("embedding") if slot.embedding else None

        plist_xml = generate_plist(model, slot, node, embedding_model=embedding_model)
        plist_name = f"com.vllm-mlx-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        # Upload plist
        result = scp_content(node.user, node.ip, plist_xml, remote_plist)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name}")
            continue

        # Bootout old (ignore error if not loaded)
        ssh_run(node.user, node.ip, f"launchctl bootout gui/{uid}/com.vllm-mlx-{slot.port} 2>/dev/null || true")

        # Bootstrap new
        result = ssh_run(node.user, node.ip,
                          f"launchctl bootstrap gui/{uid} ~/Library/LaunchAgents/{plist_name}")
        if result.returncode != 0:
            errors.append(f"{node_name}: launchctl bootstrap failed for port {slot.port}: {result.stderr.strip()}")

        deployed_ports.add(slot.port)

    # Deploy newsyslog config (requires sudo — pipe content via SSH to sudo tee)
    newsyslog = NEWSYSLOG_CONF.format(user=node.user)
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{node.user}@{node.ip}", "sudo tee /etc/newsyslog.d/vllm-mlx.conf > /dev/null"],
        input=newsyslog, capture_output=True, text=True, timeout=15,
    )

    # Clean stale plists
    ls_result = ssh_run(node.user, node.ip, "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true")
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            # Extract port from filename: com.vllm-mlx-8000.plist -> 8000
            try:
                port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    ssh_run(node.user, node.ip, f"launchctl bootout gui/{uid}/com.vllm-mlx-{port} 2>/dev/null || true")
                    ssh_run(node.user, node.ip, f"rm ~/Library/LaunchAgents/com.vllm-mlx-{port}.plist")
            except ValueError:
                continue

    return errors


def restart_litellm(config: ClusterConfig) -> bool:
    """Restart the LiteLLM container on rock."""
    rock = config.rock
    result = ssh_run(
        rock.user, rock.ip,
        "cd ~/thunder-forge/docker && docker compose restart litellm",
        timeout=60,
    )
    return result.returncode == 0


def health_poll(ip: str, port: int, *, timeout_secs: int = 180, interval: int = 5) -> bool:
    """Poll a vllm-mlx endpoint until it responds healthy.

    Returns True if healthy within timeout, False otherwise.
    """
    import time
    import urllib.request

    url = f"http://{ip}:{port}/v1/models"
    deadline = time.monotonic() + timeout_secs

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return True
        except Exception:
            time.sleep(interval)

    return False


def run_deploy(config: ClusterConfig, *, target_node: str | None = None) -> bool:
    """Run the full deploy sequence.

    Returns True if all succeeded, False otherwise.
    """
    all_ok = True

    # Determine which nodes to deploy
    if target_node:
        if target_node not in config.assignments:
            print(f"❌ Node '{target_node}' not found in assignments")
            return False
        deploy_nodes = [target_node]
    else:
        deploy_nodes = list(config.assignments.keys())

    # Deploy plists to each node
    for node_name in deploy_nodes:
        print(f"\n🚀 Deploying to {node_name}...")
        errors = deploy_node(node_name, config)
        if errors:
            all_ok = False
            for err in errors:
                print(f"  ❌ {err}")
        else:
            print(f"  ✅ Plists deployed")

    # Restart LiteLLM
    print("\n🔄 Restarting LiteLLM...")
    if restart_litellm(config):
        print("  ✅ LiteLLM restarted")
    else:
        print("  ❌ LiteLLM restart failed")
        all_ok = False

    # Health poll all nodes (not just deployed ones)
    print("\n⏳ Waiting for services to become healthy...")
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            healthy = health_poll(node.ip, slot.port)
            status = "✅" if healthy else "⚠️ timeout"
            print(f"  {node_name}:{slot.port} ({slot.model}): {status}")
            if not healthy:
                all_ok = False

    return all_ok
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_deploy.py -v
# Expected: all PASS
```

- [ ] **Step 5: Wire the `deploy` command in CLI**

Update `src/thunder_forge/cli.py` `deploy` function:

```python
@app.command()
def deploy(
    node: Optional[str] = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    from thunder_forge.cluster.config import (
        find_repo_root,
        generate_litellm_config,
        load_cluster_config,
        validate_memory,
    )
    from thunder_forge.cluster.deploy import run_deploy
    from thunder_forge.cluster.models import run_ensure_models

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config_path = repo_root / "configs" / "litellm-config.yaml"
    config = load_cluster_config(assignments_path)

    # Step 1: Ensure models (filter to target node if specified)
    typer.echo("📦 Ensuring models are present...")
    if not run_ensure_models(config, target_node=node):
        typer.echo("❌ Model sync failed", err=True)
        raise typer.Exit(1)

    # Step 2: Generate config
    typer.echo("\n📝 Generating config...")
    errors = validate_memory(config)
    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)
    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"  ✅ Generated {config_path}")

    # Steps 3-7: Deploy
    success = run_deploy(config, target_node=node)
    raise typer.Exit(0 if success else 1)
```

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py tests/test_deploy.py src/thunder_forge/cli.py
git commit -m "feat: add deploy command with plist generation and SSH orchestration"
```

---

## Chunk 6: Bootstrap Script and GitHub Actions

### Task 13: `setup-node.sh`

**Files:**
- Create: `scripts/setup-node.sh`

- [ ] **Step 1: Write the bootstrap script**

`scripts/setup-node.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Thunder Forge — Node Bootstrap Script
# Usage:
#   bash setup-node.sh inference   # Mac Studio inference node
#   bash setup-node.sh infra       # Radxa ROCK infrastructure node

ROLE="${1:-}"

if [[ -z "$ROLE" ]]; then
    echo "Usage: $0 <inference|infra>"
    exit 1
fi

echo "=== Thunder Forge Node Bootstrap ==="
echo "Role: $ROLE"
echo ""

setup_inference() {
    echo "--- Setting up inference node (macOS) ---"
    echo ""

    # 1. Homebrew
    if ! command -v brew &>/dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        echo "✅ Homebrew already installed"
    fi

    # 2. uv
    if ! command -v uv &>/dev/null; then
        echo "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
    else
        echo "✅ uv already installed"
    fi

    # 3. vllm-mlx
    if ! command -v vllm-mlx &>/dev/null; then
        echo "Installing vllm-mlx..."
        uv tool install vllm-mlx
    else
        echo "✅ vllm-mlx already installed"
    fi

    # 4. Disable macOS sleep
    echo "Disabling macOS sleep..."
    sudo pmset -a sleep 0 displaysleep 0 disksleep 0

    # 5. Create logs directory
    mkdir -p ~/logs

    echo ""
    echo "=== Inference node setup complete ==="
    echo "  Homebrew: $(brew --version | head -1)"
    echo "  uv:       $(uv --version)"
    echo "  vllm-mlx: $(vllm-mlx --version 2>/dev/null || echo 'installed')"
    echo "  Logs:     ~/logs"
    echo ""
    echo "Next steps:"
    echo "  1. Ensure SSH key from rock is in ~/.ssh/authorized_keys"
    echo "  2. Run 'thunder-forge deploy --node <this-node>' from rock"
}

setup_infra() {
    echo "--- Setting up infra node (Linux ARM64) ---"
    echo ""

    # 1. Docker Engine
    if ! command -v docker &>/dev/null; then
        echo "Installing Docker Engine..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        echo "⚠️  Log out and back in for docker group to take effect,"
        echo "   or run: newgrp docker"
    else
        echo "✅ Docker already installed"
    fi

    # 2. uv
    if ! command -v uv &>/dev/null; then
        echo "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        # Add to shell profile
        if [[ -f ~/.zshrc ]]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
        else
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
        fi
    else
        echo "✅ uv already installed"
    fi

    # 3. Clone thunder-forge
    if [[ ! -d ~/thunder-forge ]]; then
        echo "Cloning thunder-forge..."
        git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
    else
        echo "✅ thunder-forge already cloned"
        cd ~/thunder-forge && git pull
    fi

    # 4. Install dependencies
    cd ~/thunder-forge
    echo "Installing Python dependencies..."
    uv sync

    # 5. Generate docker/.env with random secrets
    if [[ ! -f ~/thunder-forge/docker/.env ]]; then
        echo "Generating docker/.env with random secrets..."
        cat > ~/thunder-forge/docker/.env <<ENVEOF
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
UI_USERNAME=admin
UI_PASSWORD=$(openssl rand -hex 8)
WEBUI_SECRET_KEY=$(openssl rand -hex 16)
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENVEOF
        echo "  ⚠️  Save these credentials! See ~/thunder-forge/docker/.env"
    else
        echo "✅ docker/.env already exists"
    fi

    # 6. Start Docker Compose
    echo "Starting Docker Compose stack..."
    cd ~/thunder-forge/docker
    docker compose up -d

    # 7. Generate SSH key
    if [[ ! -f ~/.ssh/id_ed25519 ]]; then
        echo "Generating SSH key..."
        ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
    else
        echo "✅ SSH key already exists"
    fi

    echo ""
    echo "=== Infra node setup complete ==="
    echo "  Docker:  $(docker --version)"
    echo "  uv:      $(uv --version)"
    echo "  Compose: running (check: docker compose ps)"
    echo ""
    echo "Next steps:"
    echo "  1. Copy SSH public key to inference nodes:"
    echo "     for ip in 192.168.1.{101,102,103,104}; do"
    echo "       ssh-copy-id -i ~/.ssh/id_ed25519 admin@\$ip"
    echo "     done"
    echo "  2. Run: uv run thunder-forge ensure-models"
    echo "  3. Run: uv run thunder-forge deploy"
    echo "  4. Set up GitHub Actions runner (needs token from GitHub UI)"
}

case "$ROLE" in
    inference) setup_inference ;;
    infra)     setup_infra ;;
    *)
        echo "Unknown role: $ROLE"
        echo "Usage: $0 <inference|infra>"
        exit 1
        ;;
esac
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x scripts/setup-node.sh
git add scripts/setup-node.sh
git commit -m "feat: add setup-node.sh bootstrap script"
```

---

### Task 14: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Write the deploy workflow**

`.github/workflows/deploy.yml` — copy the exact YAML from spec section 7:

```yaml
name: Deploy Cluster

on:
  push:
    branches: [main]
    paths:
      - 'configs/**'
      - 'src/thunder_forge/**'
      - 'docker/**'
  workflow_dispatch:

concurrency:
  group: deploy-cluster
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: [self-hosted, Linux, ARM64, infra]
    timeout-minutes: 30

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install dependencies
        run: uv sync

      - name: Validate configs
        run: |
          uv run thunder-forge generate-config --check
          cd docker && docker compose config --quiet

      - name: Ensure models
        run: uv run thunder-forge ensure-models
        timeout-minutes: 120

      - name: Deploy
        run: uv run thunder-forge deploy

      - name: Health check
        run: |
          sleep 10
          uv run thunder-forge health

      - name: Report
        if: always()
        run: |
          echo "## Deploy Summary" >> $GITHUB_STEP_SUMMARY
          echo "- **Trigger:** ${{ github.event_name }}" >> $GITHUB_STEP_SUMMARY
          echo "- **Commit:** ${{ github.sha }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo '```' >> $GITHUB_STEP_SUMMARY
          uv run thunder-forge health 2>&1 >> $GITHUB_STEP_SUMMARY || true
          echo '```' >> $GITHUB_STEP_SUMMARY
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: add GitHub Actions deploy workflow"
```

---

### Task 15: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append cluster CLI documentation to README**

Keep existing README content, append a section describing the new CLI. Include:
- What this is (cluster management CLI, not the old Telegram bot)
- Quick start (`uv run thunder-forge --help`)
- Commands overview (generate-config, ensure-models, deploy, health)
- Node bootstrap (`scripts/setup-node.sh`)
- Link to spec doc

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for cluster CLI"
```

---

### Task 16: Copy spec to project

**Files:**
- Create: `docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md`

- [ ] **Step 1: Copy the spec from the vault**

```bash
mkdir -p docs/specs
cp /Users/gnezim/_projects/gnezim/knowledge/projects/personal/inference-cluster/specs/2026-03-14-thunder-forge-cluster-cli-design.md \
   docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md
```

- [ ] **Step 2: Commit**

```bash
git add docs/
git commit -m "docs: add design spec and implementation plan"
```

---

### Task 17: Run all tests and final verification

- [ ] **Step 1: Run all tests**

```bash
uv run pytest tests/ -v
# Expected: all PASS
```

- [ ] **Step 2: Run ruff linter**

```bash
uv run ruff check src/ tests/
# Expected: no errors (or fix any that appear)
```

- [ ] **Step 3: Verify full CLI works**

```bash
uv run thunder-forge --help
uv run thunder-forge generate-config
uv run thunder-forge generate-config --check
# Expected: all work without errors
```

- [ ] **Step 4: Final commit if any fixes**

```bash
git add -A
git commit -m "chore: fix linting and test issues"
```
