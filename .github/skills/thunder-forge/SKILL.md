---
name: thunder-forge
description: "Use when: working on Thunder Forge v2; operating or refactoring the local oMLX/Olla inference cluster; selecting HuggingFace MLX models; evaluating memory, coder, or agent roles; checking 128 GB Apple Silicon no-swap budgets; planning staged migration from TF v1 to TF v2; managing artifacts, runtime smoke tests, Olla config, or TF edge."
---

# Thunder Forge

Use this skill for Thunder Forge v2 development, operations, model selection, and production migration planning.

Thunder Forge v2 manages a local MLX inference cluster with oMLX node runtimes, Olla routing, and a TF edge proxy.

```text
Client -> Caddy -> TF edge -> Olla -> oMLX nodes
```

## Standard Commands

Run commands from the Thunder Forge repo root.

```bash
uv sync
uv run thunder-forge --help
uv run thunder-forge artifact download --model <hf-repo> --apply
uv run thunder-forge cluster sync infer-01 --apply
uv run thunder-forge runtime status --node infer-01
uv run thunder-forge runtime restart --node infer-01 --apply
uv run thunder-forge runtime setup-daemon --node infer-01 --admin-user <admin> --apply
uv run thunder-forge runtime restart --node infer-01 --manager daemon --apply
uv run thunder-forge service restart --service omlx --node infer-01 --manager daemon --apply
uv run thunder-forge runtime smoke --node infer-01 --model <model>
make olla-install
make olla-restart
make bootstrap infer-01
make restart infer-01
make smoke infer-01
make sync infer-01
uv run thunder-forge service restart --service olla --binary .tmp/olla-bin/olla --config configs/olla-config.yaml --apply
uv run thunder-forge service restart --service edge --apply
uv run thunder-forge olla dev-smoke --binary .tmp/olla-bin/olla --model <model> --alias <alias>
uv run thunder-forge edge keys --client <client-id>
uv run thunder-forge generate-olla-config
uv run thunder-forge edge serve
uv run thunder-forge edge smoke --client-id <client-id> --model memory
uv run thunder-forge edge usage
make dev-check
```

Use implemented Thunder Forge commands or Make targets for normal production work. Avoid manual `ssh`, `rsync`, `launchctl`, or direct file moves unless the TF command does not exist yet; when that happens, document the missing target and prefer adding it.

`make sync <node>` is the preferred operator wrapper for model cache sync. It syncs every model assigned to the node by default and follows `operations.sync.restart_runtime` for the post-sync oMLX restart so the freshly synced cache is visible. Use `uv run thunder-forge cluster sync <node> --model mlx-community/<repo>` for a one-repo sync or `--no-restart-runtime` for a one-off skip. After changing `tfconfig.yaml` model placement or node topology, run `make restart gateway-cache-01` (or full `make restart`) so Olla and TF edge reload the generated router config before `make smoke <node>`.

TF edge API keys live in ignored `.env` lines named `TF_USER_<CLIENT>`, mapping client ids to API keys. Generate local clients with `make edge-keys EDGE_CLIENTS="client-a client-b"` or `uv run thunder-forge edge keys --client <client-id>`. Do not print key values. Edge JSONL accounting records include `client_id`, model, status, latency, and Olla endpoint but no API keys; summarize with `make edge-usage` or `uv run thunder-forge edge usage`.

Keep `.env` secrets-only. Non-secret local configuration lives in root `tfconfig.yaml` (ignored), mirrored by tracked `tfconfig.example.yaml`. Generated runtime configs live under ignored `configs/`. Service defaults live under `services:`: `services.olla.port` (`40115`), `services.olla.version`, `services.olla.bin_dir`, `services.edge.port` (`40116`), `services.omlx.port` (`8018`), `services.edge.access_log` (`logs/tf-edge-access.jsonl`), and optional `services.frontend.admin_user` (`gateway-admin` on `gateway-cache-01`). Operator defaults live under `operations:` for smoke and sync behavior; do not put model IDs, client IDs, transport choices, or restart policy in the Makefile. Config node roles are `gateway`, `cache`, and `inference`; use `roles: [gateway, cache]` for `gateway-cache-01` and `roles: [inference]` for oMLX-serving nodes. Explicit CLI `--port` flags and explicit `nodes.<node>.runtime.port` config values win over shared defaults.

Runtime restart managers:

- `process` is the default no-GUI/no-sudo SSH path. It manages a user-owned detached `omlx serve` process and is good for dev recovery, but it is not reboot durable.
- `daemon` is the preferred production path after node setup grants narrow `sudo -n` rights for `/usr/bin/install` and `/bin/launchctl`. It installs `/Library/LaunchDaemons/com.thunder-forge.omlx-<port>.plist`, runs oMLX as the configured node user via `UserName`, and manages `system/com.thunder-forge.omlx-<port>` through launchd.
- `launchd` is the user LaunchAgent path; use it only when the remote user launchd domain is known to accept SSH-managed services.

Use `service setup-daemon --apply --allow-sudo-prompt` for one-time gateway setup. It installs frontend Olla/Edge system LaunchDaemons through `services.frontend.admin_user`, validates sudoers with `visudo -cf`, and writes `/etc/sudoers.d/thunder-forge` with only the exact install/launchctl rights needed by future no-prompt gateway repairs. Use `runtime setup-daemon --node <node>` for one-time inference node setup; it defaults to configured `nodes.<node>.admin_user` over direct admin SSH, generates a node-side admin script, validates sudoers with `visudo -cf`, installs the system LaunchDaemon, and writes the same `/etc/sudoers.d/thunder-forge` path on that node. Add `--via-su` only when direct admin SSH is not available and SSH must connect as the node user before running `su - <admin> -c 'sudo /bin/zsh <script>'` on the node.

Use `service restart` as the unified operator command when managing full TF services. `service restart --service olla --apply` and `service restart --service edge --apply` manage frontend LaunchAgents locally as the current user; `--manager daemon` reinstalls/restarts reboot-durable system LaunchDaemons through preinstalled narrow sudoers and should not prompt. `service restart --service omlx --node <node> --manager daemon --apply` delegates to the durable oMLX node daemon path and also expects existing narrow sudoers. `make bootstrap` is the first-install/admin path for gateway + nodes; `make restart` is the repeatable reinstall/restart path and should be no-prompt once bootstrap has run. Run password-prompting system-daemon setup from a real terminal, not through VS Code guarded execution.

## Current Topology

Thunder Forge v2 has three operational roles.

- `frontend`: Caddy ingress, TF edge, Olla, routing config, auth/accounting, and external API surface.
- `cache/download`: artifact preparation under `~/.omlx/models/<owner>/<repo>` plus sync to inference nodes; this can be a script/CLI workflow, not a daemon.
- `inference node`: oMLX node-level daemon serving the local model set.

Example compact role placement:

- `gateway-cache-01`: `frontend` and `cache/download`.
- `infer-01`-`infer-04`: inference nodes, with `infer-03` as the first TF v2 proof node.

Example split production placement:

- `gateway-01`: `frontend`.
- `cache-01`: `cache/download`, using oMLX/HF tooling and syncing through Thunderbolt fabric when available, Wi-Fi fallback otherwise.
- `infer-01`-`infer-04`: inference nodes.

Thunder Forge v2 migration is staged.

- `infer-01`: existing production inference node.
- `infer-02`: existing production inference node.
- `infer-03`: dedicated TF v2 proof node.
- `infer-04`: reserved direct oMLX node until migration.

Production migration order after `infer-03` tests and real use cases are stable:

1. Move `infer-01` into TF v2 and validate.
2. Move `infer-02` into TF v2 and validate.
3. Move `infer-04` from direct oMLX into TF v2 and validate.

Validate each node before moving to the next one: artifact status/download/sync, runtime install/start/status/smoke, Olla config generation, Olla smoke, and TF edge smoke.

## Canonical Roles

Use exactly these role names unless the config explicitly says otherwise:

- `memory`: Hindsight retain/reflect/consolidation LLM. This is the canonical Hindsight role name; do not create a second Hindsight memory alias unless a future compatibility need is explicit.
- `coder`: coding, code review, and long dev sessions.
- `agent`: tool calling, structured output, self-correction, and long-context autonomous work.

Temporary benchmark aliases such as `memory-bf16` may exist in `models` and `nodes.<node>.models`, but they are not canonical roles and should stay clearly marked as comparison routes.

## Target Role Spread

All example inference nodes are 128 GB Apple Silicon nodes. The role budget numbers are required runtime RAM on the node, not model names.

| Node | Roles | Budget Intent |
|------|-------|---------------|
| `infer-01` | `memory`, `coder` | memory about 20 GB runtime RAM; coder about 40-90 GB runtime RAM |
| `infer-02` | `memory`, `coder` | memory about 20 GB runtime RAM; coder about 40-90 GB runtime RAM |
| `infer-03` | `memory`, `agent` | memory about 20 GB runtime RAM; agent about 40-90 GB runtime RAM |
| `infer-04` | `memory`, `agent` | memory about 20 GB runtime RAM; agent about 40-90 GB runtime RAM |

Role placement must prevent swap. Account for model weights, KV cache, MLX overhead, OS headroom, and the node's paired heavy role.

## Role-Aware Balancing

The balancer should keep every major role ready, not just pick the least busy endpoint.

Expected behavior:

- Every major role should have ready capacity.
- `memory` is replicated because Hindsight is important and relatively small.
- `coder` capacity should be preserved on `infer-01`/`infer-02`.
- `agent` capacity should be preserved on `infer-03`/`infer-04`.
- If a memory request arrives while `infer-01` is busy, routing should prefer a healthy memory replica on `infer-03` or `infer-04` when that keeps coder capacity available.

Model recommendations and routing changes should mention the impact on the node's paired heavy role and on swap risk.

## Artifact Identity

Use oMLX's native download layout as the TF cache and sync layout:

```text
~/.omlx/models/<namespace>/<repo-name>
```

Example:

```text
~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8
```

The cache host's `~/.omlx/models` is the cache hub for downloads and node sync. Do not use the old `hf--<namespace>--<repo>` direct-child layout in new TF code.

oMLX discovers nested `<namespace>/<repo-name>` directories and exposes the repo directory name as the runtime model id, for example `gpt-oss-20b-MXFP4-Q8`. Requests that include a provider prefix can still resolve because oMLX strips the prefix if needed, but TF `models.<id>.runtime_model_id` should use the visible runtime id.

Separate these concepts in code and docs:

- HF `repo_id`
- oMLX artifact directory path under `~/.omlx/models`
- runtime model id seen by oMLX/Olla (`repo-name` for nested layout)
- public model alias seen by clients (`models.<id>`)

For TF v2, model placement lives on each node as `nodes.<node>.models`. Do not add a separate `assignments`, `runtime_routes`, or role-group layer for Olla routing; those shapes came from older per-model-service stacks and obscure the node-level oMLX runtime model.

## Sync And Transport

Sync should prefer Thunderbolt/fabric when possible.

Recommended behavior:

1. Treat `fabric_host` as a boolean config flag, not a hostname.
2. If `fabric_host: true`, discover reachable link-local Thunderbolt addresses through the management host.
3. Use fabric if reachable.
4. Fall back to management host with a visible reason.
5. If `fabric_host` is false or absent, do not probe fabric in `auto` mode.
6. Fail hard when the user explicitly forces fabric and probing is disabled or unreachable.

Artifact readiness should check completeness, not only directory existence. Check expected config/tokenizer files, shard presence, no `.incomplete` files, and source/target size or manifest consistency.

## Model Selection Rules

Default posture: prefer SOTA, actively maintained HuggingFace MLX models, then reject anything that does not fit local constraints.

Hard constraints:

- Native MLX or known working MLX conversion from HuggingFace.
- Fits inside 128 GB node RAM with no swap under expected context.
- Leaves headroom for OS, MLX overhead, KV cache growth, and the node's paired role.
- Can be smoke-tested through oMLX, then Olla, then TF edge for its role alias.
- Does not require a serving path outside standard TF v2 commands unless the gap is documented as a missing TF feature.

Useful HF scan pattern:

```text
https://huggingface.co/models?pipeline_tag=text-generation&library=mlx&num_parameters=min:12B,max:128B&sort=downloads
```

Prioritize:

- `mlx-community/*`
- `lmstudio-community/*` when the conversion is healthier or only available there
- recent instruct/tool models
- downloads and real usage over likes
- current benchmarks over stale claims

Cross-check against:

- Hindsight memory/retain leaderboards for `memory`.
- SWE-bench Verified, LiveCodeBench, and HumanEval-style evidence for `coder`.
- BFCL, structured-output evidence, Arena/LiveBench, and tool-calling reports for `agent`.

## Benchmark-Driven Selection

For each role, rank candidate models by the primary benchmark for that role. Secondary benchmarks break ties.

```text
role     primary benchmark          secondary benchmark        why
------   ------------------------   ------------------------   -----------------------
agent    MMLU + tool_use/reasoning  BFCL V4, SWE-bench         general knowledge + structured output + coding
coder    SWE-bench Verified         LiveCodeBench, HumanEval   real-world code, real repos, real issues
memory   Hindsight retain leaderboard (separate benchmark)    task-specific for fact extraction & consolidation
```

### Benchmark meaning

**MMLU (Massive Multitask Language Understanding)** — general knowledge across 57 subjects (math, law, medicine, CS, philosophy, history) from high-school to expert level. Multiple choice, scale 0–100. Scores below 75 are weak for agent/memory roles where structured reasoning and fact extraction are critical. Scores 85+ are SOTA.

**SWE-bench Verified** — real-world coding. The model receives a GitHub issue from production open-source repos (Django, scikit-learn, sympy, Flask), must navigate the codebase, find the bug, and write a valid patch. Not synthetic. Scores below 50% are entry-level coding assistants; 70%+ is SOTA open-source agentic coding.

### GPU/hardware filter

Use `https://whatmodelscanirun.com/` with filters matching the cluster hardware to produce a ranked candidate list per role:

```text
# Apple M4 Max 128GB (our nodes)
https://whatmodelscanirun.com/?gpu=m4-max&mem=3&feat=tool_use%2Creasoning

# Alternative filter for coding emphasis
https://whatmodelscanirun.com/?gpu=m4-max&mem=3&feat=tool_use
```

The site returns a ranked table with MMLU, quantization, context window, and tok/s per model. Use this as the **initial candidate shortlist** before applying hard constraints (MLX format, no-swap RAM budget, oMLX compatibility smoke).

### Candidate shortlist (whatmodelscanirun.com, M4 Max + tool_use + reasoning, May 2026)

Top tier (MMLU >80, both features supported):

| Model | MMLU | Speed | Context | Role candidate |
|---|---|---|---|---|
| Qwen3.6 27B | 86.2 | ~31 tok/s | 256K | memory (upgrade path), agent-dense |
| Gemma 4 31B | 85.2 | ~28 tok/s | 256K | agent-dense (+ vision) |
| Qwen3.6 35B A3B (MoE) | 85.2 | ~187 tok/s | 256K | agent (current) |
| Qwen3 32B | 85.0 | ~26 tok/s | 128K | agent-dense |
| Step 3.5 Flash 197B | 84.0 | ~5 tok/s | 52K | reject (too slow) |
| QwQ 32B | 83.0 | ~26 tok/s | 128K | reject (narrow — reasoning only) |

Strong but missing features (use when feature is not needed):

| Model | MMLU | Speed | Missing |
|---|---|---|---|
| Qwen3 Next 80B | 90 | ~131-194 tok/s | reasoning |
| Qwen3.5 122B A10B | 85 | ~114-188 tok/s | tool_use, reasoning |
| Qwen 2.5 72B | 86.1 | 7-11 tok/s | reasoning |

Weak for structured output (reject for agent/memory unless proven otherwise):

- GPT-OSS 20B: MMLU 75, no native tool_use or reasoning — current `memory` model, known JSON parse failures during Hindsight consolidation.

### Memory role — separate benchmark layer

`memory` uses **Hindsight's own leaderboards** as the primary signal (see `hindsight-internals` skill, references on model benchmarking). MMLU is the secondary cross-check because Hindsight consolidation is a specific structured-fact-extraction task.

Known finding (May 2026): current `memory` model (GPT-OSS 20B, MMLU 75) produces systematic JSON parse errors during consolidation (~40% of consolidation batches fail). This is a model capability issue, not a quantization artifact — candidate replacements must have MMLU ≥ 85 AND native tool_use.

## RAM Budget Method

Use this rough budget before proposing a model:

```text
usable_node_ram = 128 GB - 10 GB OS/headroom - 3 GB MLX overhead - safety_margin
role_total_ram = model_weights + kv_cache_at_expected_context + serving_overhead
```

Preferred operating target:

- `memory`: about 20 GB runtime RAM.
- `coder`: 40-90 GB runtime RAM.
- `agent`: 40-90 GB runtime RAM.
- Combined loaded role budget on one node should stay comfortably below 128 GB and avoid swap.

Swap is a hard warning. If a candidate needs swap during loading or at expected context, reject it for that role/node pairing.

## Current Role Defaults

### memory

Current preferred model:

- HF: `mlx-community/gpt-oss-20b-MXFP4-Q8`
- TF-managed runtime model id: `gpt-oss-20b-MXFP4-Q8`
- Serving: standard `omlx serve`
- Purpose: Hindsight retain/reflect/consolidation LLM, not embeddings
- Budget: roughly 20 GB runtime RAM at practical Hindsight context
- Note: reasoning models may return `reasoning_content` instead of `content`; smoke tests must accept both

Prefer this over `lmstudio-community/gpt-oss-20b-MLX-8bit` unless the 8-bit quality gain is proven worth the extra RAM and swap pressure.

Benchmark route:

- Alias: `memory-bf16`
- HF: `mlx-community/gpt-oss-20b-mxfp4-bf16`
- Runtime model id: `gpt-oss-20b-mxfp4-bf16`
- Purpose: compare quality/latency/RAM against the canonical Q8 `memory` route on `infer-03`; do not promote until benchmarked.

### Original openai/gpt-oss-20b

`openai/gpt-oss-20b` is the original/reference HF repo.

Observed metadata on 2026-05-28:

- HF repo: `openai/gpt-oss-20b`
- Library: `transformers`, not `mlx`
- License: Apache 2.0
- Architecture: `GptOssForCausalLM`
- Quantization: MXFP4 for MoE weights
- Parameters: 21B total, 3.6B active per the model card
- Context: 131072 max position embeddings
- Model card claim: runs within 16 GB of memory
- Supported paths in the card: Transformers, vLLM, Ollama, LM Studio, OpenAI reference runtime

Fit assessment:

- RAM fit: likely yes for the `memory` role on a 128 GB node, especially at practical Hindsight context.
- TF v2 fit: not the preferred default because the repo is not a native MLX conversion and may require a non-standard serving path.
- Recommendation: keep `mlx-community/gpt-oss-20b-MXFP4-Q8` as the default `memory` model for standard oMLX/Olla operations. Treat `openai/gpt-oss-20b` as the upstream/reference model or a future candidate only after oMLX compatibility is proven through TF commands.

### coder

Current preferred model:

- HF: `mlx-community/Qwen3-Coder-Next-4bit`
- Purpose: repository coding, code review, long multi-turn development
- Requirements: strong SWE-bench/LiveCodeBench style performance, stable 128K+ context, tool-friendly behavior
- Budget: 40-90 GB runtime RAM depending on context

Fast/secondary candidate:

- HF: `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`

### agent

Current candidate set:

- HF: `mlx-community/Qwen3.6-35B-A3B-4bit`
- HF: `mlx-community/Qwen3.5-122B-A10B-4bit`

Requirements:

- Reliable function/tool calling and JSON schema adherence.
- Instruction following under long multi-step tasks.
- Self-correction and clear failure behavior.
- BFCL, SWE-bench, Arena/LiveBench, or comparable evidence.
- Fits a 40-90 GB runtime RAM budget without swap.

## Recommendation Format

When proposing a model, include:

- role: `memory`, `coder`, or `agent`
- HF repo id and revision
- expected runtime model id
- estimated weights, KV cache, total RAM, and no-swap headroom
- target nodes and paired role impact
- benchmark evidence
- oMLX/Olla/TF edge smoke plan
- risks or rejection reasons

## Pitfalls

- Do not confuse the role name `memory` with embeddings. It is the Hindsight LLM role.
- Do not recommend a second Hindsight memory alias unless compatibility requires it later.
- Do not treat MoE active parameters as total RAM. All weights still need memory.
- Do not rely on a model's advertised context without real long-context testing.
- Do not blame quantization before checking sync completeness. Partial artifacts can look like model incompatibility.
- Do not use Llama-4-Scout on oMLX nodes unless compatibility is re-proven; prior attempts triggered validation/swap problems.
- Do not manually SSH or rsync for normal operations. Prefer TF CLI/Make targets; add a missing target when the workflow is not covered.
- HF cache snapshots contain symlinks to blobs; promotion/copy flows must preserve or dereference correctly.
- oMLX may need restart to discover newly synced models.
