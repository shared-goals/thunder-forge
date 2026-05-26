# msm3 artifact readiness and fabric inspection — 2026-05-26

## Scope

Development work was performed from `shag@studio` against `shag@msm3` only. Production `rock` and `msm4` were not touched.

## SSH host identity

`msm3-wifi.lan` was added to `~/.ssh/known_hosts` after verifying its scanned ed25519 host key matched the already-known `msm3-wifi` host key. No new SSH identity key was created; existing SSH config / `id_key` remains the identity source.

## Cache observations

Studio HF cache initially contained:

- `BAAI/bge-small-en-v1.5`
- `cross-encoder/ms-marco-MiniLM-L-6-v2`

`msm3` HF cache contained several MLX Qwen artifacts, including:

- `mlx-community/Qwen3.6-35B-A3B-4bit`
- `mlx-community/Qwen3-Coder-Next-4bit`
- `mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ`

Neither studio nor `msm3` had `~/.omlx/models` at inspection time.

Artifact status examples:

- `BAAI/bge-small-en-v1.5`: present on studio, missing on msm3 → next action `sync_to_node`.
- `mlx-community/Qwen3.6-35B-A3B-4bit`: missing on studio, present in msm3 HF cache, missing oMLX model dir → current planner says `download_to_studio` because studio is defined as MVP cache source.
- `mlx-community/gpt-oss-20b-MXFP4-Q8`: missing in both caches → next action `download_to_studio`.

## New small model download

Downloaded `mlx-community/Qwen3-1.7B-4bit` to studio via:

```bash
env -u ALL_PROXY -u all_proxy uvx --from huggingface_hub hf download mlx-community/Qwen3-1.7B-4bit
```

Reason for unsetting `ALL_PROXY`: `huggingface_hub` uses `httpx`; SOCKS proxy env without `socksio` causes `ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`.

Resulting studio cache path:

```text
~/.cache/huggingface/hub/models--mlx-community--Qwen3-1.7B-4bit/snapshots/3b1b1768f8f8cf8351c712464f906e86c2b8269e
```

Planner now reports this model present on studio and missing on `msm3`, so next action is `sync_to_node`.

## studio ↔ msm3 fabric discovery

Observed link-local fabric path:

- studio side: `en7` / `169.254.91.93`
- msm3 side: `en12` / `169.254.251.195`

Verification:

```text
studio -> 169.254.251.195 ping: ok, ~1.3–1.6 ms
msm3 -> 169.254.91.93 ping: ok, ~1.2–1.5 ms
ssh 169.254.251.195: ok as shag, host key matched msm3
```

A small safe copy over the fabric path succeeded:

```bash
scp ~/.cache/huggingface/hub/models--mlx-community--Qwen3-1.7B-4bit/refs/main \
  169.254.251.195:/Users/shag/tf-fabric-smoke/qwen3-1.7b-ref-main
```

Remote verification:

```text
40 /Users/shag/tf-fabric-smoke/qwen3-1.7b-ref-main
3b1b1768f8f8cf8351c712464f906e86c2b8269e
```

## Follow-up design notes

- Keep `.lan` hostnames for homelab configs.
- `fabric_host` should eventually be a durable alias/static IP, not a transient link-local literal.
- Current artifact planner is intentionally read-only. Sync/download actions should be introduced in separate TDD slices.
- The planner currently prioritizes studio as cache source. A future improvement can recognize node-present/studio-missing as an import/backfill candidate, but that is a separate decision.
