# oMLX Runtime MVP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a dev-only Thunder Forge MVP that runs one already cached MLX artifact through oMLX on `msm3`, then optionally exposes it through LiteLLM on `studio`.

**Architecture:** Thunder Forge remains the control plane. oMLX is modeled as a node-level daemon on `msm3`, not as one service per model. `studio` is the dev frontend/future cache hub. `msm4` remains dedicated to direct oMLX/Hindsight and `rock` production is not touched. The first operator interface is CLI with stable JSON and dry-run/apply semantics; API, MCP, web UI, PostgreSQL, and queues are deferred until the simpler shape is insufficient.

**Tech Stack:** Python 3.12+, Typer, pytest, httpx, existing Thunder Forge SSH helpers, oMLX CLI/server, optional LiteLLM dev route generation.

---

## Constraints and guardrails

- Work only in `/Users/shag/Work/thunder-forge` on `studio`.
- Do not modify production `rock` for this MVP.
- Do not disturb `msm4`; it is the dedicated direct oMLX/Hindsight node.
- Use `uv run`, never raw `python` or `pytest`, inside this repo.
- Use existing `ssh_run` / `scp_content` helpers for remote operations; do not introduce ad-hoc subprocess SSH wrappers.
- Use `.lan` names only for normal LAN-resolved hosts.
- Treat `msm3-wifi.lan` as stable management/bootstrap path.
- Treat future Thunderbolt/fabric paths as point-to-point, not necessarily `.lan`; do not assume any fabric hostname exists until `/etc/hosts`, mDNS, or another macOS mapping is configured.
- Do not download or sync new model weights until read-only cache inspection works.
- Start from the existing `shag@msm3` cache. Observed candidates include `mlx-community/Qwen3.6-35B-A3B-4bit`, `mlx-community/Qwen3-30B-A3B-4bit`, and other Qwen3 artifacts.
- Prefer dev port `8018` on `msm3` until stale `admin`-owned MLX processes are fully removed.
- Do not touch Hindsight production traffic during this MVP.
- Keep public docs framed around operator expectations and system behavior, not personal user stories.
- Treat the FastAPI/PostgreSQL/React/jobs design sketch as a useful backlog, not as MVP scope.

## Task 1: Record the dev topology, operator expectations, and msm3/msm4 split in docs

**Objective:** Keep PRD/ADR aligned with the agreed split and public-doc language before code changes.

**Files:**
- Modify: `docs/prd/2026-05-24-omlx-runtime-mvp.md`
- Modify: `docs/adr/0001-omlx-node-runtime.md`
- Create/modify: `docs/notes/2026-05-25-msm3-dev-node-inspection.md`

**Steps:**
1. Ensure the PRD says the first TF v2 dev use case runs an already cached model on `msm3`.
2. Ensure the PRD describes operator expectations rather than a personal named-user story.
3. Ensure the PRD captures the final expectation: Thunder Forge as compute resource for Shared Goals platform, `whattodo`, and `text-forge`, with Daily Compass / operations summaries.
4. Ensure the ADR says oMLX is node-level runtime and `msm4` is excluded from dev experiments while dedicated to Hindsight.
5. Verify docs mention `msm3-wifi.lan` as stable LAN management and fabric aliasing as an unresolved point-to-point setup, not necessarily `.lan`.
6. Record `msm3` live facts: `uv`, oMLX metadata, cache candidates, stale admin-process caveat.
7. Run:
   ```bash
   git diff -- docs/prd/2026-05-24-omlx-runtime-mvp.md docs/adr/0001-omlx-node-runtime.md docs/notes/2026-05-25-msm3-dev-node-inspection.md
   ```

**Expected:** Diff shows doc-only updates and no production `rock` changes.

## Task 2: Inspect current config and deployment code

**Objective:** Identify the smallest extension points for oMLX without rewriting current runtime logic.

**Files:**
- Read: `src/thunder_forge/cli.py`
- Read: `src/thunder_forge/cluster/config.py`
- Read: `src/thunder_forge/cluster/deploy.py`
- Read: `src/thunder_forge/cluster/health.py`
- Read: `src/thunder_forge/cluster/models.py`
- Read: relevant tests under `tests/`

**Steps:**
1. Read current CLI command definitions.
2. Read config models and LiteLLM generation.
3. Read health check implementation.
4. Read model sync/download implementation.
5. Write brief implementation notes before changing code.

**Expected:** Clear decision on whether to add `runtime.py`, extend `health.py`, or add separate `omlx.py` module.

## Task 3: Add a config schema draft for node runtime identity

**Objective:** Represent stable management host, optional future fabric host, and node-level runtime without changing production config behavior.

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify/Create: `tests/test_config.py` or `tests/test_runtime_config.py`
- Modify: `configs/node-assignments.yaml.example`

**Step 1: Write failing tests**

Add tests for parsing a node like:

```yaml
nodes:
  msm3:
    host: msm3-wifi.lan
    fabric_host: msm3-fabric
    runtime:
      type: omlx
      port: 8018
      model_dir: /Users/shag/.cache/thunder-forge/models
```

Assertions:

```python
assert node.host == "msm3-wifi.lan"
assert node.fabric_host == "msm3-fabric"
assert node.runtime.type == "omlx"
assert node.runtime.port == 8018
```

**Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_config.py -q
```

Expected: FAIL because runtime/fabric fields do not exist yet.

**Step 3: Implement minimal schema**

Add optional fields only. Do not break existing configs.

**Step 4: Run tests**

```bash
uv run pytest tests/test_config.py -q
```

Expected: PASS.

## Task 4: Add oMLX direct health client

**Objective:** Check oMLX node daemon health over HTTP, independently of LiteLLM.

**Files:**
- Create: `src/thunder_forge/cluster/omlx.py`
- Create: `tests/test_omlx.py`

**Step 1: Write failing tests**

Use `httpx.MockTransport` or equivalent to test:

- `GET /health` success;
- `GET /v1/models` success;
- failed request returns structured failure;
- `/v1/models/status` optional failure does not hide `/health` state.

**Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_omlx.py -q
```

Expected: FAIL because module does not exist.

**Step 3: Implement minimal client**

Functions/classes should return typed/simple results, for example:

```python
@dataclass
class OmlxHealthResult:
    base_url: str
    health_ok: bool
    models_ok: bool
    status_ok: bool | None
    models: list[str]
    errors: list[str]
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_omlx.py -q
```

Expected: PASS.

## Task 5: Add `runtime status` CLI for oMLX nodes

**Objective:** Let Shag inspect `msm3` oMLX readiness from the dev repo.

**Files:**
- Modify: `src/thunder_forge/cli.py`
- Modify/Create: `tests/test_cli_runtime.py`

**Step 1: Write failing test**

Test command shape only, with mocked oMLX client:

```bash
uv run thunder-forge runtime status --node msm3
```

Expected output includes:

```text
node: msm3
runtime: omlx
management_host: msm3-wifi.lan
fabric_host: msm3-fabric
base_url: http://msm3-wifi.lan:8018
health: ok
```

**Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_cli_runtime.py -q
```

Expected: FAIL because command does not exist.

**Step 3: Implement thin CLI wrapper**

CLI should load config, find node, build base URL from runtime config, call oMLX health client, and print readable output.

**Step 4: Run tests**

```bash
uv run pytest tests/test_cli_runtime.py -q
```

Expected: PASS.

## Task 6: Add model-cache inspection command for `msm3`

**Objective:** Discover which existing `shag@msm3` Hugging Face cache entries can be used directly by oMLX or need a resolved snapshot/model directory.

**Files:**
- Create or modify: `src/thunder_forge/cluster/models.py`
- Modify: `src/thunder_forge/cli.py`
- Create/modify tests under `tests/`

**Command shape:**

```bash
uv run thunder-forge model inspect-cache --node msm3 --model mlx-community/Qwen3.6-35B-A3B-4bit
```

**Step 1: Write failing tests**

Mock SSH output for candidate directories. Assert the command reports:

- candidate path;
- owner;
- readability by `shag`;
- whether it contains expected Hugging Face cache structure (`blobs`, `refs`, `snapshots`);
- resolved snapshot path from `refs/main` or an explicit revision;
- whether the resolved snapshot contains expected MLX model files;
- whether `.incomplete` files exist under the cache entry;
- recommended next action: use resolved snapshot dir, move/copy, download later, or reject incomplete cache.

**Step 2: Implement inspect-only logic**

Do not move/chown files in this task. This is read-only discovery plus snapshot resolution.

**Step 3: Run tests**

```bash
uv run pytest tests/test_models.py -q
```

Expected: PASS.

## Task 7: Add oMLX run command using existing model dir

**Objective:** Start or dry-run the command to start oMLX on `msm3` with the selected model directory.

**Files:**
- Create/modify: `src/thunder_forge/cluster/omlx.py`
- Modify: `src/thunder_forge/cli.py`
- Create/modify tests under `tests/`

**Command shape:**

```bash
uv run thunder-forge runtime start --node msm3 --model-dir <path> --port 8018 --dry-run
```

**Step 1: Write failing tests**

Assert generated remote command is equivalent to:

```bash
/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018 --model-dir <path>
```

Do not include SSD KV cache flags yet.

**Step 2: Implement minimal start path**

For the first implementation, default to dry-run unless explicitly told to start a process.

**Step 3: Run tests**

```bash
uv run pytest tests/test_omlx.py tests/test_cli_runtime.py -q
```

Expected: PASS.

## Task 8: Add direct memory/agent-like smoke test

**Objective:** Verify that the selected cached model behaves acceptably for agent/runtime tasks through direct oMLX.

**Files:**
- Modify: `src/thunder_forge/cluster/omlx.py`
- Modify: `src/thunder_forge/cli.py`
- Create/modify: `tests/test_omlx.py`

**Command shape:**

```bash
uv run thunder-forge runtime smoke --node msm3 --model <selected-model-id>
```

**Smoke checks:**

- `/health`;
- `/v1/models`;
- `/v1/chat/completions` with deterministic short prompt;
- `/v1/responses` if available;
- JSON-ish output test;
- reject raw internal tokens like `<|channel|>analysis`;
- classify `There is no Stream(gpu, 1) in current thread` as hard fail.

**Expected:** CLI reports pass/fail with reasons, not just a green HTTP status.

## Task 9: Add compatibility matrix recording

**Objective:** Store runtime/model evidence before any runtime promotion.

**Files:**
- Create: `docs/compatibility/omlx-msm3-dev-model.md`
- Optionally create CLI writer later, but start with doc template.

**Template fields:**

```markdown
# oMLX Compatibility: msm3 dev model

- Runtime artifact:
- Cache path:
- Resolved snapshot path:
- Date:
- Node: msm3
- Runtime host: msm3-wifi.lan
- Port: 8018
- Model dir:
- oMLX version:
- Direct /health:
- Direct /v1/models:
- Direct chat:
- Direct responses:
- JSON-ish output:
- Internal token cleanliness:
- LiteLLM route:
- LiteLLM chat:
- Decision:
- Notes:
```

## Task 10: Add optional LiteLLM dev route only after direct smoke passes

**Objective:** Expose the selected model through LiteLLM under a test name after direct oMLX is healthy.

**Files:**
- Modify: config generation code after direct runtime path exists.
- Modify/create tests around LiteLLM generation.

**Route name pattern:**

```text
<model-short-name>-omlx-msm3-test
```

**Guardrail:** Do not modify production LiteLLM config or Hindsight config.

## Task 11: Add utilization and audit summary design

**Objective:** Define the smallest CLI/API contract that can feed Daily Compass and operator review before adding databases or dashboards.

**Files:**
- Create: `docs/operations/daily-summary-contract.md`
- Later modify CLI after runtime MVP works.

**Initial fields:**

```json
{
  "period": "YYYY-MM-DD",
  "requests_total": 0,
  "callers": [],
  "workloads": [],
  "models": [],
  "nodes": [],
  "failures": [],
  "model_freshness_notes": [],
  "configuration_changes": []
}
```

**Guardrail:** Start as a document/contract. Do not add PostgreSQL, log ingestion, or a web dashboard until the direct runtime and LiteLLM route are working and we know which data already exists in LiteLLM/oMLX logs.

## Final verification

Run:

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
git status --short --branch
git diff
```

Expected:

- Tests pass.
- Ruff passes.
- Diff is reviewable.
- No production `rock` files changed.
- No Hindsight/`msm4` runtime changes are included.
