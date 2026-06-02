# Code inspection: TF v2/oMLX extension points

Date: 2026-05-25
Branch: `feature/omlx-runtime-mvp`
Scope: local dev repo on `studio` only. No production `rock` or `msm4` runtime changes.

## Current code shape

- `src/thunder_forge/cli.py`
  - Flat Typer command surface: `generate-config`, `ensure-models`, `deploy`, `restart`, `stop`, `health`.
  - Older inspection snapshot: this has since moved to root `tfconfig.yaml` through `_load_config()`.
  - Existing commands operate on assignment slots: mostly one launchd service per `{model, port}`.

- `src/thunder_forge/cluster/config.py`
  - Dataclass-based parser with `Node`, `Model`, `Assignment`, `ClusterConfig`.
  - `Node.host` is the required host field in the current v2 schema.
  - Current node schema has no `fabric_host` and no node-level `runtime` identity.
  - LiteLLM generation is assignment/port based and should remain stable for current TF.

- `src/thunder_forge/cluster/deploy.py`
  - Contains proven launchd and stale-port handling patterns.
  - Generates one plist per model assignment using `mlx_lm.server` or `mlx-openai-server`.
  - Good reusable techniques: resolved `home_dir`, `homebrew_prefix`, launchd bootout/kill/bootstrap ordering, dry-run output, direct `/v1/models` polling.
  - Not a good fit to bend into oMLX directly because oMLX is node-level runtime, not one service per model assignment.

- `src/thunder_forge/cluster/health.py`
  - Simple HTTP `/v1/models` probes and gateway Docker health checks.
  - Useful pattern, but oMLX should return structured health (`/health`, `/v1/models`, optional `/v1/models/status`) rather than only boolean slot checks.

- `src/thunder_forge/cluster/models.py`
  - Proven HF cache/download/sync code uses existing SSH helpers and `.lan` host conventions.
  - For oMLX MVP, first add read-only cache inspection/resolution before mutating caches.

## Decision

Add a separate `src/thunder_forge/cluster/omlx.py` module for oMLX-specific behavior.

Reasons:

1. KISS: keep oMLX concepts in one small module instead of mixing node-level runtime semantics into slot-level deploy/health code.
2. DRY: reuse existing config parser and `ssh_run` helpers, but avoid duplicating launchd logic until a real apply/start path needs it.
3. YAGNI: implement schema parsing and dry-run/start command construction first; defer launchd service management, queues, database, and LiteLLM integration.
4. Safety: preserves existing production-compatible assignment/deploy code while TF v2 schema evolves in the dev branch.

## Initial implementation direction

- Extend `Node` minimally with:
  - `fabric_host: bool`
  - `runtime: NodeRuntime | None`
- Add `RuntimeType` / `NodeRuntime` in `config.py`.
- Keep `runtime.model_dir is None` as the normal oMLX default: serve command should omit `--model-dir` unless explicitly configured.
- Create `cluster/omlx.py` for:
  - `build_omlx_serve_command(node)`
  - later direct HTTP health client
  - later SSH dry-run/apply wrapper
- Add `runtime` Typer subgroup later rather than overloading existing `deploy`.
