# Thunder Forge v2 — msm3 Dev Node Inspection

## 2026-05-25 role split

`msm4-wifi` is now the dedicated direct oMLX runtime for Hindsight. New Thunder Forge v2/oMLX development tests move to `msm3-wifi` so Hindsight stability and TF runtime experiments do not interfere with each other.

## Host facts

- Host: `msm3-wifi.lan`
- User: `shag` (`uid=502`, group `staff`)
- macOS: `26.5` (`25F71`)
- Arch: `arm64`
- Disk: ~658 GiB available on the data volume during inspection

## `shag` runtime setup

Installed under `shag@msm3`:

```text
/Users/shag/.local/bin/uv  -> uv 0.11.16
/Users/shag/.local/bin/uvx -> uvx 0.11.16
/Users/shag/.local/bin/omlx -> oMLX CLI installed via uv tool from git commit 27f31996c1975fbf5ff977b0289e5c488559093e
```

`omlx --help` works and exposes:

```text
omlx {serve,launch,diagnose}
```

`omlx --version` is not a supported command; use uv tool metadata instead. Current metadata:

```text
omlx v0.3.10
```

## Hugging Face cache under `shag`

Cache root is writable by `shag`:

```text
/Users/shag/.cache/huggingface/hub
```

Observed cached artifacts:

```text
mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ        ~335M
mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit   ~13G
mlx-community/Qwen3-30B-A3B-4bit                  ~16G
mlx-community/Qwen3.5-35B-A3B-4bit                ~19G
mlx-community/Qwen3.6-35B-A3B-4bit                ~19G
mlx-community/Qwen3-Coder-Next-4bit               ~42G
mlx-community/Qwen3.5-122B-A10B-4bit              ~71G
```

Each observed model directory has one `snapshots/<revision>` directory. Some `.incomplete` files remain from older cache activity, so model-cache inspection must validate the resolved snapshot before using it.

## Port selection

Prefer starting TF v2/oMLX experiments on an isolated dev port such as `8018` until runtime ownership and launchd/service management are represented explicitly in Thunder Forge config.

## Next implementation implication

The immediate TF v2 implementation should target:

```text
studio dev control plane -> msm3-wifi.lan oMLX runtime -> optional studio LiteLLM dev route
```

Guardrails:

- Do not touch production `rock`.
- Do not disturb `msm4`; it is Hindsight's direct oMLX node.
- Start with read-only cache inspection and direct oMLX health/smoke commands.
- Keep launchd/service management under `shag`, not `admin`.
