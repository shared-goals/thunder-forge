# Thunder Forge oMLX Runtime MVP — Live Notes

## 2026-05-24 msm4 inspection

### Cache state

The `gpt-oss` MLX artifact is present under `shag@msm4`:

```text
/Users/shag/.cache/huggingface/hub/models--mlx-community--gpt-oss-20b-MXFP4-Q8
```

Resolved snapshot:

```text
/Users/shag/.cache/huggingface/hub/models--mlx-community--gpt-oss-20b-MXFP4-Q8/snapshots/773a7da77e569019bb0fd17a554b263738d669a3
```

Snapshot contains:

- `config.json`
- `generation_config.json`
- `model.safetensors.index.json`
- `model-00001-of-00003.safetensors`
- `model-00002-of-00003.safetensors`
- `model-00003-of-00003.safetensors`
- `tokenizer.json`
- `tokenizer_config.json`
- `special_tokens_map.json`
- `chat_template.jinja`

Model facts:

- `model_type`: `gpt_oss`
- architecture: `GptOssForCausalLM`
- artifact size: ~11 GB cache, ~12.1 GB safetensors total size
- quantization: MXFP4 with Q8-ish mixed overrides
- context: `max_position_embeddings = 131072`

### Fabric / Thunderbolt state

Physical Thunderbolt is connected.

On `msm4`, `system_profiler SPThunderboltDataType` shows bus 3 connected at 80 Gb/s with `Internet Protocol` service.

Working fabric-ish path from `studio` to `msm4`:

```text
studio -> 169.254.191.158 -> msm4
```

Verified:

```text
ping 169.254.191.158: ok, ~1.4–2.0 ms
ssh 169.254.191.158: ok as shag
```

This is not `.lan`; it is point-to-point/link-local. Do not assume stable DNS. Candidate later setup: static per-pair IP + `/etc/hosts` aliases such as `msm4-fabric` and `studio-fabric-msm4`.

### oMLX install state

`omlx` is not installed for `shag@msm4`.

Relevant install facts:

- oMLX README official CLI path: `brew tap jundot/omlx https://github.com/jundot/omlx && brew install omlx`.
- macOS app does not install CLI.
- `pyproject.toml` requires Python `>=3.11` and exposes CLI entry point `omlx = omlx.cli:main`.
- `shag@msm4` default SSH PATH is only `/usr/bin:/bin:/usr/sbin:/sbin`; system `python3` is 3.9.6.
- `/opt/homebrew/bin/brew` exists but is owned by/admin-installed; `shag` may not be able to install with it.
- Existing admin uv exists at `/Users/admin/.local/bin/uv`, not suitable as final `shag` runtime owner.

Preferred new-TF direction: install/update oMLX as an explicit Thunder Forge runtime feature under `shag`, not as a Homebrew service. Candidate sequence to validate:

1. install `uv` for `shag`;
2. install oMLX CLI from source or PyPI/git into `shag` tool environment;
3. verify `omlx --version`;
4. launch explicit `omlx serve` under a `shag` LaunchAgent managed by Thunder Forge.

Homebrew CLI install can be a fallback, but avoid `brew services start omlx` for cluster runtime because Thunder Forge should own launchd orchestration.

## 2026-05-24 dry install probe for `shag@msm4`

Probe facts:

- user/home: `shag` / `/Users/shag`
- default SSH PATH: `/usr/bin:/bin:/usr/sbin:/sbin`
- shell: `/bin/zsh`
- macOS: `26.5`, arm64
- system Python: `/usr/bin/python3`, version `3.9.6` — too old for oMLX (`>=3.11`)
- CLT present: `/Library/Developer/CommandLineTools`, Apple clang available
- network/download basics available: `/usr/bin/curl`, `/usr/bin/git`, `/usr/bin/tar`, `/usr/bin/unzip`
- `xz` not found in default PATH
- no proxy/HF env variables visible in non-interactive SSH env
- `/Users/admin/.local/bin/uv` exists (`0.10.11`) but belongs to admin and is not the target runtime owner
- no `uv` in `shag` PATH
- no `omlx` in `shag` PATH
- `~/.local` does not exist yet, but parent `/Users/shag` is writable, so user-local install can create it
- `/usr/local/bin` is missing/not writable for `shag`; `/opt/homebrew/bin` exists but is admin-owned/not writable

Conclusion: install under `shag` home first. Do not rely on system Python or admin Homebrew for the MVP runtime owner.

Candidate next command for user-local `uv` install on `msm4` as `shag`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv --version
```

Then install oMLX under the same user-local tool environment. Preferred candidate to validate:

```bash
~/.local/bin/uv tool install "git+https://github.com/jundot/omlx.git"
~/.local/bin/omlx --version
```

If that fails because oMLX expects Homebrew-packaged resources or build isolation cannot resolve git deps, fallback is Homebrew CLI install, but not `brew services`; Thunder Forge should still own launchd/process management.

## 2026-05-25 oMLX install and smoke results

Installed under `shag@msm4`:

```text
/Users/shag/.local/bin/uv  -> uv 0.11.16
/Users/shag/.local/bin/uvx -> uvx 0.11.16
/Users/shag/.local/bin/omlx -> oMLX CLI installed via uv tool from git
```

Package versions from metadata:

- `omlx 0.3.10`
- `mlx 0.31.2`
- `mlx-lm 0.31.3`
- `mlx-vlm 0.5.0`
- `mlx-embeddings 0.1.0`
- `openai-harmony 0.0.8`

`omlx --version` is not supported; use package metadata from the uv tool venv.

Model directory setup:

```text
/Users/shag/.omlx/models/gpt-oss-20b-MXFP4-Q8
  -> /Users/shag/.cache/huggingface/hub/models--mlx-community--gpt-oss-20b-MXFP4-Q8/snapshots/773a7da77e569019bb0fd17a554b263738d669a3
```

Started explicit dev server under `shag`:

```bash
omlx serve \
  --model-dir /Users/shag/.omlx/models \
  --host 0.0.0.0 \
  --port 8017 \
  --no-cache \
  --max-concurrent-requests 1 \
  --log-level info
```

Server health via LAN path:

```text
GET http://msm4-wifi.lan:8017/health -> 200
status: healthy
default_model: gpt-oss-20b-MXFP4-Q8
model_count: 1
loaded_count before generation: 0
```

Models:

```json
{
  "id": "gpt-oss-20b-MXFP4-Q8",
  "owned_by": "omlx"
}
```

Chat smoke:

- First load: `Reply exactly: ok`, `max_tokens=20` returned HTTP 200 but only `reasoning_content`; `finish_reason=length`; no final `content`. This is a token budget issue, not a crash.
- With `max_tokens=100`, chat returned `content: "ok"` and separate `reasoning_content`, `finish_reason=stop`, ~0.8s after model loaded.

Responses smoke:

- `/v1/responses` returned HTTP 200 with clean final `output_text: "ok"`.
- Usage included separate `reasoning_tokens`.

JSON/Hindsight-like smoke:

Prompt: return compact JSON with `ok`, `summary`, `confidence`.

- Chat returned final content: `{"ok":true,"summary":"memory smoke","confidence":0.9}` plus separate `reasoning_content`.
- Responses returned clean `output_text` with the same valid compact JSON and no leaked Harmony tokens.

Important implication for Hindsight/Hermes:

- The Responses API path is cleaner for final-only output.
- Chat API is usable, but clients must ignore `reasoning_content` and require enough `max_tokens` for final channel emission. Too-low `max_tokens` can consume the budget in reasoning and return no final content.

Fabric note:

- `/etc/hosts` on `studio` currently has `169.254.191.158 msm4-fabric`, but the link-local path became unstable during testing: `ping 169.254.191.158` later failed and ARP stayed incomplete on `en6`.
- LAN path `msm4-wifi.lan:8017` remained healthy.
- Static point-to-point fabric IPs are still needed before using fabric in TF config.
