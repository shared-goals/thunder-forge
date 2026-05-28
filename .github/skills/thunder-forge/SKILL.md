---
name: thunder-forge
description: "Use when: working on Thunder Forge v2; operating or refactoring the local oMLX/Olla inference cluster; selecting HuggingFace MLX models; evaluating memory, coder, or agent roles; checking 128 GB Mac Studio no-swap budgets; planning staged migration from TF v1 to TF v2; managing artifacts, runtime smoke tests, Olla config, or TF edge."
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
uv run thunder-forge artifact sync --model <hf-repo> --node msm3 --apply
uv run thunder-forge runtime status --node msm3
uv run thunder-forge runtime restart --node msm3 --apply
uv run thunder-forge runtime setup-daemon --node msm3 --admin-user <admin> --apply
uv run thunder-forge runtime restart --node msm3 --manager daemon --apply
uv run thunder-forge runtime smoke --node msm3 --model <model>
uv run thunder-forge olla dev-smoke --binary <path> --model <model> --alias <alias>
uv run thunder-forge edge keys --client <client-id>
uv run thunder-forge generate-olla-config
uv run thunder-forge edge serve --olla-base-url http://127.0.0.1:40115
uv run thunder-forge edge smoke --base-url http://127.0.0.1:40116 --client-id <client-id> --model memory
uv run thunder-forge edge usage
uv run pytest --tb=short -q
uv run ruff check .
```

Use implemented Thunder Forge commands or Make targets for normal production work. Avoid manual `ssh`, `rsync`, `launchctl`, or direct file moves unless the TF command does not exist yet; when that happens, document the missing target and prefer adding it.

TF edge MVP API keys live in one ignored `.env` JSON hash named `TF_USERS`, mapping client ids to API keys. Generate local clients with `make edge-keys EDGE_CLIENTS="client-a client-b"` or `uv run thunder-forge edge keys --client <client-id>`. Do not print key values. Edge JSONL accounting records include `client_id`, model, status, latency, and Olla endpoint but no API keys; summarize with `make edge-usage` or `uv run thunder-forge edge usage`.

Runtime restart managers:

- `process` is the default no-GUI/no-sudo SSH path. It manages a user-owned detached `omlx serve` process and is good for dev recovery, but it is not reboot durable.
- `daemon` is the preferred production path after node setup grants narrow `sudo -n` rights for `/usr/bin/install` and `/bin/launchctl`. It installs `/Library/LaunchDaemons/com.thunder-forge.omlx-<port>.plist`, runs oMLX as the configured node user via `UserName`, and manages `system/com.thunder-forge.omlx-<port>` through launchd.
- `launchd` is the user LaunchAgent path; use it only when the remote user launchd domain is known to accept SSH-managed services.

Use `runtime setup-daemon --node <node> --admin-user <admin>` for the one-time production setup. It generates a node-side admin script, validates sudoers with `visudo -cf`, installs the system LaunchDaemon, and installs the narrow sudoers include used by future `runtime restart --manager daemon` calls. Add `--via-su` when SSH should connect as the node user and then run `su - <admin> -c 'sudo /bin/zsh <script>'` on the node.

## Current Topology

Thunder Forge v2 migration is staged.

- `studio`: dev control plane and cache hub; downloads, syncs, and can run TF edge.
- `msm1`: current TF v1 production node.
- `msm2`: current TF v1 production node.
- `msm3`: dedicated TF v2 dev node.
- `msm4`: direct oMLX node for Hindsight today.
- `rock`: production infra; do not touch for TF v2 dev.

Production migration order after `msm3` tests and real use cases are stable:

1. Move `msm1` into TF v2 and validate.
2. Move `msm2` into TF v2 and validate.
3. Move `msm4` from direct Hindsight oMLX into TF v2 and validate.

Validate each node before moving to the next one: artifact status/download/sync, runtime install/start/status/smoke, Olla config generation, Olla smoke, and TF edge smoke.

## Canonical Roles

Use exactly these role names unless the config explicitly says otherwise:

- `memory`: Hindsight retain/reflect/consolidation LLM. This is the canonical Hindsight role name; do not create a second Hindsight memory alias unless a future compatibility need is explicit.
- `coder`: coding, code review, and long dev sessions.
- `agent`: tool calling, structured output, self-correction, and long-context autonomous work.

Temporary benchmark aliases such as `memory-bf16` may exist in `runtime_routes`, but they are not canonical roles and should stay clearly marked as comparison routes.

## Target Role Spread

All MSM nodes are 128 GB Mac Studio nodes. The role budget numbers are required runtime RAM on the node, not model names.

| Node | Roles | Budget Intent |
|------|-------|---------------|
| `msm1` | `memory`, `coder` | memory about 20 GB runtime RAM; coder about 40-90 GB runtime RAM |
| `msm2` | `memory`, `coder` | memory about 20 GB runtime RAM; coder about 40-90 GB runtime RAM |
| `msm3` | `memory`, `agent` | memory about 20 GB runtime RAM; agent about 40-90 GB runtime RAM |
| `msm4` | `memory`, `agent` | memory about 20 GB runtime RAM; agent about 40-90 GB runtime RAM |

Role placement must prevent swap. Account for model weights, KV cache, MLX overhead, OS headroom, and the node's paired heavy role.

## Role-Aware Balancing

The balancer should keep every major role ready, not just pick the least busy endpoint.

Expected behavior:

- Every major role should have ready capacity.
- `memory` is replicated because Hindsight is important and relatively small.
- `coder` capacity should be preserved on `msm1`/`msm2`.
- `agent` capacity should be preserved on `msm3`/`msm4`.
- If a memory request arrives while `msm1` is busy, routing should prefer a healthy memory replica on `msm3` or `msm4` when that keeps coder capacity available.

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

Studio's `~/.omlx/models` is the cache hub for downloads and node sync. Do not use the old `hf--<namespace>--<repo>` direct-child layout in new TF code.

oMLX discovers nested `<namespace>/<repo-name>` directories and exposes the repo directory name as the runtime model id, for example `gpt-oss-20b-MXFP4-Q8`. Requests that include a provider prefix can still resolve because oMLX strips the prefix if needed, but TF `runtime_routes` should use the visible runtime id.

Separate these concepts in code and docs:

- HF `repo_id`
- oMLX artifact directory path under `~/.omlx/models`
- runtime model id seen by oMLX/Olla (`repo-name` for nested layout)
- public role alias seen by clients

For TF v2, use `runtime_routes` as the single operational route layer. Do not add a separate `assignments` section for Olla routing; that shape came from the older per-model-service stack.

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
- Purpose: compare quality/latency/RAM against the canonical Q8 `memory` route on `msm3`; do not promote until benchmarked.

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
