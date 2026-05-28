# Design: Server Args Configuration in Admin UI

**Date:** 2026-03-26
**Status:** Approved

## Overview

Add the ability to configure `mlx_lm.server` CLI arguments per-model through the Thunder Forge admin UI. Currently `extra_args: list[str] | None` exists on the `Model` dataclass and is used in plist generation, but is not exposed in the UI (hardcoded to `None`).

The design uses a hybrid approach: known, typed args get dedicated UI controls; anything else goes into a raw text area as an escape hatch.

## Section 1: Data Model

### New `ServerArgs` dataclass (`src/thunder_forge/cluster/config.py`)

All fields are optional (`None` = omit flag, use mlx_lm.server default).

```python
@dataclass
class ServerArgs:
    # Concurrency
    decode_concurrency: int | None = None    # --decode-concurrency (mlx default: 32)
    prompt_concurrency: int | None = None    # --prompt-concurrency (mlx default: 8)
    prefill_step_size: int | None = None     # --prefill-step-size (mlx default: 2048)
    # KV cache
    prompt_cache_size: int | None = None     # --prompt-cache-size
    prompt_cache_bytes: int | None = None    # --prompt-cache-bytes
    # Sampling defaults
    max_tokens: int | None = None            # --max-tokens (mlx default: 512)
    temp: float | None = None               # --temp (mlx default: 0.0)
    top_p: float | None = None              # --top-p (mlx default: 1.0)
    top_k: int | None = None               # --top-k (mlx default: 0)
    min_p: float | None = None             # --min-p (mlx default: 0.0)
    # Speculative decoding
    draft_model: str | None = None          # --draft-model (HF repo or local path)
    num_draft_tokens: int | None = None     # --num-draft-tokens (mlx default: 3)
```

### `Model` dataclass update

One new field alongside the existing `extra_args`:

```python
server_args: ServerArgs | None = None   # structured named args → CLI flags
extra_args: list[str] | None = None     # existing raw passthrough (unchanged)
```

`enable_thinking` remains a separate field — it maps to `--chat-template-args` (a JSON blob), not a simple value flag, so it does not belong in `ServerArgs`.

## Section 2: Plist Generation (`src/thunder_forge/cluster/deploy.py`)

In `generate_plist()`, after the existing `enable_thinking` block and before the `extra_args` block:

```python
if model.server_args:
    sa = model.server_args
    for flag, value in [
        ("--decode-concurrency", sa.decode_concurrency),
        ("--prompt-concurrency", sa.prompt_concurrency),
        ("--prefill-step-size", sa.prefill_step_size),
        ("--prompt-cache-size", sa.prompt_cache_size),
        ("--prompt-cache-bytes", sa.prompt_cache_bytes),
        ("--max-tokens", sa.max_tokens),
        ("--temp", sa.temp),
        ("--top-p", sa.top_p),
        ("--top-k", sa.top_k),
        ("--min-p", sa.min_p),
        ("--draft-model", sa.draft_model),
        ("--num-draft-tokens", sa.num_draft_tokens),
    ]:
        if value is not None:
            program_args.extend([flag, str(value)])
```

Emission order: base args → `--chat-template-args` (thinking) → `server_args` flags → `extra_args`. This means `extra_args` can override `server_args` entries if the same flag appears in both — intentional escape hatch behavior.

All args verified against official mlx-lm source (`mlx_lm/server.py`). The full list of supported CLI args is:
`--model`, `--adapter-path`, `--host`, `--port`, `--allowed-origins`, `--draft-model`, `--num-draft-tokens`, `--trust-remote-code`, `--log-level`, `--chat-template`, `--use-default-chat-template`, `--temp`, `--top-p`, `--top-k`, `--min-p`, `--max-tokens`, `--chat-template-args`, `--decode-concurrency`, `--prompt-concurrency`, `--prefill-step-size`, `--prompt-cache-size`, `--prompt-cache-bytes`, `--pipeline`.

## Section 3: YAML / JSONB Serialization

### YAML format

`server_args` serializes as a flat dict under the model key. `None` fields are omitted for clean YAML:

```yaml
models:
  qwen3-35-moe:
    server_args:
      decode_concurrency: 48
      prompt_concurrency: 16
      max_tokens: 8192
      draft_model: "mlx-community/Qwen3-0.6B-4bit"
      num_draft_tokens: 5
    extra_args:
      - "--trust-remote-code"
```

### Deserialization (`config.py`)

```python
ServerArgs(**data["server_args"])  # same pattern as ModelSource
```

### Serialization helper

```python
def _server_args_to_dict(sa: ServerArgs) -> dict:
    return {k: v for k, v in asdict(sa).items() if v is not None}
```

### JSONB round-trip

`server_args` flows through the admin UI's JSONB storage as a plain nested dict — no special handling needed, consistent with how `source` is already handled.

## Section 4: Admin UI (`admin/thunder_admin/pages/models.py`)

### Edit Model form

A new `st.expander("Server Tuning (Advanced)", expanded=False)` section added to the existing edit form. Inside, two sub-sections:

**Named controls** — `st.number_input` / `st.text_input` per field, arranged in columns. Empty value = `None` (mlx default). Mlx defaults shown as placeholder/help text.

Layout:
```
[ Concurrency ]
  Decode concurrency  [   48  ]  help: mlx default 32
  Prompt concurrency  [   16  ]  help: mlx default 8
  Prefill step size   [ 2048  ]  help: mlx default 2048

[ KV Cache ]
  Prompt cache size   [       ]
  Prompt cache bytes  [       ]

[ Sampling Defaults ]
  Max tokens  [ 512 ]   Temp  [ 0.0 ]
  Top-p       [ 1.0 ]   Top-k [   0 ]   Min-p [ 0.0 ]

[ Speculative Decoding ]
  Draft model       [ mlx-community/... ]
  Num draft tokens  [ 3 ]  help: mlx default 3
```

**Raw extra args** — `st.text_area("Extra args (one flag per line)")`, pre-populated from `extra_args`. On save: split by newline, strip blank lines → `list[str]`. Empty text area → `None`.

### Save logic

```python
server_args = {k: v for k, v in {
    "decode_concurrency": decode_concurrency or None,
    ...
}.items() if v is not None} or None

extra_args = [l for l in raw_extra_args.splitlines() if l.strip()] or None
```

### Add Model form

Same expander added, collapsed by default, all fields empty. Gives users the option to configure tuning at model creation time without making it prominent.

## Section 5: Testing

**Plist generation tests** (in `tests/`, alongside existing `enable_thinking` tests):

- `server_args` with all fields set → correct flags appear in `ProgramArguments`
- `server_args=None` → no extra flags emitted
- `server_args` with partial fields → only non-`None` fields emitted
- `extra_args` + `server_args` both set → both appear, `extra_args` last
- `draft_model` set → `--draft-model` and `--num-draft-tokens` emitted correctly

**Config parsing tests:**

- YAML with `server_args` dict → `ServerArgs` dataclass populated correctly
- YAML without `server_args` → `model.server_args is None`
- Round-trip: `ServerArgs` → dict → `ServerArgs` (no data loss)

No UI tests — Streamlit pages are not unit tested in this codebase.
