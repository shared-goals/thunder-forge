# Server Args Admin UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `mlx_lm.server` CLI tuning arguments (concurrency, sampling, speculative decoding, etc.) as a structured `ServerArgs` dataclass in the data model and an "Advanced / Server Tuning" expander in the admin UI, with a raw `extra_args` text area for arbitrary flag passthrough.

**Architecture:** Add `ServerArgs` dataclass to `config.py` alongside `Model`; update `_parse_model` to deserialize it; update `generate_plist` to emit its flags between the `enable_thinking` block and the existing `extra_args` block; add a collapsed `st.expander` to both the Edit and Add Model forms in `models.py`.

**Tech Stack:** Python dataclasses, PyYAML, Streamlit, pytest

---

### Task 1: Add `ServerArgs` dataclass and update `_parse_model`

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `tests/test_parse_cluster_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_parse_cluster_config.py`:

```python
from thunder_forge.cluster.config import ServerArgs, parse_cluster_config


def test_parse_model_server_args_populated():
    """server_args dict in YAML becomes a ServerArgs dataclass."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
                "server_args": {
                    "decode_concurrency": 48,
                    "prompt_concurrency": 16,
                    "max_tokens": 8192,
                    "temp": 0.5,
                    "draft_model": "mlx-community/Qwen3-0.6B-4bit",
                    "num_draft_tokens": 5,
                },
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    sa = config.models["coder"].server_args
    assert sa is not None
    assert sa.decode_concurrency == 48
    assert sa.prompt_concurrency == 16
    assert sa.max_tokens == 8192
    assert sa.temp == 0.5
    assert sa.draft_model == "mlx-community/Qwen3-0.6B-4bit"
    assert sa.num_draft_tokens == 5


def test_parse_model_server_args_absent():
    """No server_args key in YAML → model.server_args is None."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    assert config.models["coder"].server_args is None


def test_parse_model_server_args_partial():
    """Partial server_args dict — unset fields are None."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
                "server_args": {"decode_concurrency": 64},
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    sa = config.models["coder"].server_args
    assert sa is not None
    assert sa.decode_concurrency == 64
    assert sa.prompt_concurrency is None
    assert sa.temp is None
    assert sa.draft_model is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_parse_cluster_config.py::test_parse_model_server_args_populated tests/test_parse_cluster_config.py::test_parse_model_server_args_absent tests/test_parse_cluster_config.py::test_parse_model_server_args_partial -v
```

Expected: FAIL — `ImportError: cannot import name 'ServerArgs'`

- [ ] **Step 3: Add `ServerArgs` dataclass and update `Model` + `_parse_model` in `config.py`**

After the `ModelSource` dataclass (line 23), insert:

```python
@dataclass
class ServerArgs:
    decode_concurrency: int | None = None    # --decode-concurrency (mlx default: 32)
    prompt_concurrency: int | None = None    # --prompt-concurrency (mlx default: 8)
    prefill_step_size: int | None = None     # --prefill-step-size (mlx default: 2048)
    prompt_cache_size: int | None = None     # --prompt-cache-size
    prompt_cache_bytes: int | None = None    # --prompt-cache-bytes
    max_tokens: int | None = None            # --max-tokens (mlx default: 512)
    temp: float | None = None               # --temp (mlx default: 0.0)
    top_p: float | None = None              # --top-p (mlx default: 1.0)
    top_k: int | None = None               # --top-k (mlx default: 0)
    min_p: float | None = None             # --min-p (mlx default: 0.0)
    draft_model: str | None = None          # --draft-model
    num_draft_tokens: int | None = None     # --num-draft-tokens (mlx default: 3)
```

Update the `Model` dataclass to add `server_args` after `enable_thinking`:

```python
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
    extra_args: list[str] | None = None
    enable_thinking: bool | None = None
    server_args: ServerArgs | None = None
```

Update `_parse_model` to deserialize `server_args`:

```python
def _parse_server_args(raw: dict) -> ServerArgs:
    return ServerArgs(
        decode_concurrency=raw.get("decode_concurrency"),
        prompt_concurrency=raw.get("prompt_concurrency"),
        prefill_step_size=raw.get("prefill_step_size"),
        prompt_cache_size=raw.get("prompt_cache_size"),
        prompt_cache_bytes=raw.get("prompt_cache_bytes"),
        max_tokens=raw.get("max_tokens"),
        temp=raw.get("temp"),
        top_p=raw.get("top_p"),
        top_k=raw.get("top_k"),
        min_p=raw.get("min_p"),
        draft_model=raw.get("draft_model"),
        num_draft_tokens=raw.get("num_draft_tokens"),
    )


def _parse_model(raw: dict) -> Model:
    server_args_raw = raw.get("server_args")
    return Model(
        source=_parse_model_source(raw["source"]),
        disk_gb=raw.get("disk_gb", 0.0),
        kv_per_32k_gb=raw.get("kv_per_32k_gb", 0.0),
        ram_gb=raw.get("ram_gb"),
        active_params=raw.get("active_params", ""),
        max_context=raw.get("max_context", 0),
        serving=raw.get("serving", ""),
        notes=raw.get("notes", ""),
        extra_args=raw.get("extra_args"),
        enable_thinking=raw.get("enable_thinking"),
        server_args=_parse_server_args(server_args_raw) if server_args_raw is not None else None,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_parse_cluster_config.py::test_parse_model_server_args_populated tests/test_parse_cluster_config.py::test_parse_model_server_args_absent tests/test_parse_cluster_config.py::test_parse_model_server_args_partial -v
```

Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/test_parse_cluster_config.py
git commit -m "feat: add ServerArgs dataclass and parse from YAML"
```

---

### Task 2: Emit `server_args` flags in plist generation

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py`
- Modify: `tests/test_deploy.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deploy.py`:

```python
from thunder_forge.cluster.config import Assignment, Model, ModelSource, Node, ServerArgs
from thunder_forge.cluster.deploy import generate_plist


def _resolved_node() -> Node:
    return Node(
        ip="192.168.1.101",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )


def test_generate_plist_server_args_all_fields() -> None:
    """All ServerArgs fields are emitted as CLI flags in ProgramArguments."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(
            decode_concurrency=48,
            prompt_concurrency=16,
            prefill_step_size=1024,
            prompt_cache_size=100,
            prompt_cache_bytes=1073741824,
            max_tokens=8192,
            temp=0.7,
            top_p=0.9,
            top_k=50,
            min_p=0.1,
            draft_model="mlx-community/Qwen3-0.6B-4bit",
            num_draft_tokens=5,
        ),
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert ">48<" in xml_str
    assert "--prompt-concurrency" in xml_str
    assert ">16<" in xml_str
    assert "--prefill-step-size" in xml_str
    assert ">1024<" in xml_str
    assert "--prompt-cache-size" in xml_str
    assert ">100<" in xml_str
    assert "--prompt-cache-bytes" in xml_str
    assert ">1073741824<" in xml_str
    assert "--max-tokens" in xml_str
    assert ">8192<" in xml_str
    assert "--temp" in xml_str
    assert ">0.7<" in xml_str
    assert "--top-p" in xml_str
    assert ">0.9<" in xml_str
    assert "--top-k" in xml_str
    assert ">50<" in xml_str
    assert "--min-p" in xml_str
    assert ">0.1<" in xml_str
    assert "--draft-model" in xml_str
    assert "mlx-community/Qwen3-0.6B-4bit" in xml_str
    assert "--num-draft-tokens" in xml_str
    assert ">5<" in xml_str


def test_generate_plist_server_args_none() -> None:
    """server_args=None emits no extra flags."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=None,
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" not in xml_str
    assert "--prompt-concurrency" not in xml_str
    assert "--max-tokens" not in xml_str


def test_generate_plist_server_args_partial() -> None:
    """Only non-None ServerArgs fields are emitted."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(decode_concurrency=64),
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert ">64<" in xml_str
    assert "--prompt-concurrency" not in xml_str
    assert "--max-tokens" not in xml_str


def test_generate_plist_server_args_before_extra_args() -> None:
    """server_args flags appear before extra_args in ProgramArguments."""
    node = _resolved_node()
    model = Model(
        source=ModelSource(type="huggingface", repo="test/model"),
        disk_gb=10,
        server_args=ServerArgs(decode_concurrency=48),
        extra_args=["--trust-remote-code"],
    )
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "--decode-concurrency" in xml_str
    assert "--trust-remote-code" in xml_str
    # decode-concurrency must appear before trust-remote-code
    assert xml_str.index("--decode-concurrency") < xml_str.index("--trust-remote-code")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_deploy.py::test_generate_plist_server_args_all_fields tests/test_deploy.py::test_generate_plist_server_args_none tests/test_deploy.py::test_generate_plist_server_args_partial tests/test_deploy.py::test_generate_plist_server_args_before_extra_args -v
```

Expected: FAIL — `ImportError: cannot import name 'ServerArgs'` (Task 1 must be done first)

- [ ] **Step 3: Update `generate_plist` in `deploy.py`**

Update the import line at the top of `deploy.py`:

```python
from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node, ServerArgs
```

In `generate_plist`, replace the block starting at line 109 (after the base `program_args` list and `enable_thinking` block, before the `extra_args` block):

```python
    if model.enable_thinking is not None:
        import json

        program_args.extend(["--chat-template-args", json.dumps({"enable_thinking": model.enable_thinking})])

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

    if model.extra_args:
        program_args.extend(model.extra_args)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_deploy.py::test_generate_plist_server_args_all_fields tests/test_deploy.py::test_generate_plist_server_args_none tests/test_deploy.py::test_generate_plist_server_args_partial tests/test_deploy.py::test_generate_plist_server_args_before_extra_args -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py tests/test_deploy.py
git commit -m "feat: emit server_args flags in plist generation"
```

---

### Task 3: Admin UI — Server Tuning expander in Edit Model form

**Files:**
- Modify: `admin/thunder_admin/pages/models.py`

The edit form currently lives inside `if st.session_state.get(f"editing_model_{name}"):` at lines 57-123. We add a `st.expander` section inside the `st.form` block after the existing `new_notes` field and before the save/cancel buttons.

- [ ] **Step 1: Add the Server Tuning expander to the Edit form**

Replace the edit form block (lines 58-123 of `models.py`) with:

```python
                if st.session_state.get(f"editing_model_{name}"):
                    with st.form(f"edit_model_{name}"):
                        new_disk = st.number_input(
                            "Disk GB",
                            value=float(model.get("disk_gb", 0)),
                            step=0.1,
                            key=f"edit_disk_{name}",
                        )
                        new_ram = st.number_input(
                            "RAM GB override (0 = auto)",
                            value=float(model.get("ram_gb", 0) or 0),
                            step=0.1,
                            key=f"edit_ram_{name}",
                        )
                        new_context = st.number_input(
                            "Max context",
                            value=model.get("max_context", 0),
                            step=1024,
                            key=f"edit_ctx_{name}",
                        )
                        new_kv = st.number_input(
                            "KV per 32k GB",
                            value=float(model.get("kv_per_32k_gb", 0)),
                            step=0.01,
                            key=f"edit_kv_{name}",
                        )
                        new_serving = st.selectbox(
                            "Serving",
                            ["", "embedding", "cli", "mlx-openai-server"],
                            index=["", "embedding", "cli", "mlx-openai-server"].index(model.get("serving", ""))
                            if model.get("serving", "") in ["", "embedding", "cli", "mlx-openai-server"]
                            else 0,
                            key=f"edit_srv_{name}",
                        )
                        _THINKING_OPTIONS = ["Default", "Enabled", "Disabled"]
                        _THINKING_FROM_VAL = {None: 0, True: 1, False: 2}
                        new_thinking_label = st.selectbox(
                            "Thinking mode",
                            _THINKING_OPTIONS,
                            index=_THINKING_FROM_VAL.get(model.get("enable_thinking"), 0),
                            key=f"edit_thinking_{name}",
                        )
                        new_notes = st.text_area(
                            "Notes",
                            value=model.get("notes", ""),
                            key=f"edit_notes_{name}",
                        )

                        with st.expander("Server Tuning (Advanced)", expanded=False):
                            sa = model.get("server_args") or {}
                            st.caption("Leave blank to use mlx_lm.server defaults.")
                            c1, c2, c3 = st.columns(3)
                            new_decode_concurrency = c1.number_input(
                                "Decode concurrency", value=int(sa.get("decode_concurrency") or 0),
                                min_value=0, step=1, help="mlx default: 32. 0 = use default.",
                                key=f"edit_sa_decode_{name}",
                            )
                            new_prompt_concurrency = c2.number_input(
                                "Prompt concurrency", value=int(sa.get("prompt_concurrency") or 0),
                                min_value=0, step=1, help="mlx default: 8. 0 = use default.",
                                key=f"edit_sa_prompt_{name}",
                            )
                            new_prefill_step = c3.number_input(
                                "Prefill step size", value=int(sa.get("prefill_step_size") or 0),
                                min_value=0, step=256, help="mlx default: 2048. 0 = use default.",
                                key=f"edit_sa_prefill_{name}",
                            )
                            c4, c5 = st.columns(2)
                            new_cache_size = c4.number_input(
                                "Prompt cache size", value=int(sa.get("prompt_cache_size") or 0),
                                min_value=0, step=1, help="KV cache entry count. 0 = use default.",
                                key=f"edit_sa_cache_size_{name}",
                            )
                            new_cache_bytes = c5.number_input(
                                "Prompt cache bytes", value=int(sa.get("prompt_cache_bytes") or 0),
                                min_value=0, step=1073741824, help="KV cache size in bytes. 0 = use default.",
                                key=f"edit_sa_cache_bytes_{name}",
                            )
                            st.markdown("**Sampling defaults**")
                            c6, c7, c8, c9, c10 = st.columns(5)
                            new_max_tokens = c6.number_input(
                                "Max tokens", value=int(sa.get("max_tokens") or 0),
                                min_value=0, step=256, help="mlx default: 512. 0 = use default.",
                                key=f"edit_sa_max_tokens_{name}",
                            )
                            new_temp = c7.number_input(
                                "Temp", value=float(sa.get("temp") or 0.0),
                                min_value=0.0, max_value=2.0, step=0.05, help="mlx default: 0.0",
                                key=f"edit_sa_temp_{name}",
                            )
                            new_top_p = c8.number_input(
                                "Top-p", value=float(sa.get("top_p") or 0.0),
                                min_value=0.0, max_value=1.0, step=0.05, help="mlx default: 1.0. 0 = use default.",
                                key=f"edit_sa_top_p_{name}",
                            )
                            new_top_k = c9.number_input(
                                "Top-k", value=int(sa.get("top_k") or 0),
                                min_value=0, step=1, help="mlx default: 0 (disabled)",
                                key=f"edit_sa_top_k_{name}",
                            )
                            new_min_p = c10.number_input(
                                "Min-p", value=float(sa.get("min_p") or 0.0),
                                min_value=0.0, max_value=1.0, step=0.01, help="mlx default: 0.0 (disabled)",
                                key=f"edit_sa_min_p_{name}",
                            )
                            st.markdown("**Speculative decoding**")
                            c11, c12 = st.columns(2)
                            new_draft_model = c11.text_input(
                                "Draft model", value=sa.get("draft_model") or "",
                                help="HF repo or local path for speculative decoding draft model.",
                                key=f"edit_sa_draft_model_{name}",
                            )
                            new_num_draft_tokens = c12.number_input(
                                "Num draft tokens", value=int(sa.get("num_draft_tokens") or 0),
                                min_value=0, step=1, help="mlx default: 3. 0 = use default.",
                                key=f"edit_sa_num_draft_{name}",
                            )
                            existing_extra = "\n".join(model.get("extra_args") or [])
                            new_extra_args_text = st.text_area(
                                "Extra args (one flag per line)",
                                value=existing_extra,
                                help="Arbitrary mlx_lm.server flags. These are appended after all named args above.",
                                key=f"edit_sa_extra_{name}",
                            )

                        col_save, col_cancel = st.columns(2)
                        if col_save.form_submit_button("Save"):
                            model["disk_gb"] = new_disk
                            model["ram_gb"] = new_ram if new_ram > 0 else None
                            model["max_context"] = new_context
                            model["kv_per_32k_gb"] = new_kv
                            model["serving"] = new_serving
                            new_thinking = {"Default": None, "Enabled": True, "Disabled": False}[new_thinking_label]
                            if new_thinking is None:
                                model.pop("enable_thinking", None)
                            else:
                                model["enable_thinking"] = new_thinking
                            model["notes"] = new_notes

                            # Build server_args dict — only non-zero/non-empty values
                            new_sa = {}
                            if new_decode_concurrency > 0:
                                new_sa["decode_concurrency"] = new_decode_concurrency
                            if new_prompt_concurrency > 0:
                                new_sa["prompt_concurrency"] = new_prompt_concurrency
                            if new_prefill_step > 0:
                                new_sa["prefill_step_size"] = new_prefill_step
                            if new_cache_size > 0:
                                new_sa["prompt_cache_size"] = new_cache_size
                            if new_cache_bytes > 0:
                                new_sa["prompt_cache_bytes"] = new_cache_bytes
                            if new_max_tokens > 0:
                                new_sa["max_tokens"] = new_max_tokens
                            if new_temp > 0.0:
                                new_sa["temp"] = new_temp
                            if new_top_p > 0.0:
                                new_sa["top_p"] = new_top_p
                            if new_top_k > 0:
                                new_sa["top_k"] = new_top_k
                            if new_min_p > 0.0:
                                new_sa["min_p"] = new_min_p
                            if new_draft_model.strip():
                                new_sa["draft_model"] = new_draft_model.strip()
                            if new_num_draft_tokens > 0:
                                new_sa["num_draft_tokens"] = new_num_draft_tokens
                            model["server_args"] = new_sa if new_sa else None

                            # Parse extra_args text area
                            parsed_extra = [l for l in new_extra_args_text.splitlines() if l.strip()]
                            model["extra_args"] = parsed_extra if parsed_extra else None

                            if save_config_or_error(st, config, user, f"Updated model '{name}'"):
                                del st.session_state[f"editing_model_{name}"]
                                st.success(f"Updated '{name}'")
                                st.rerun()
                        if col_cancel.form_submit_button("Cancel"):
                            del st.session_state[f"editing_model_{name}"]
                            st.rerun()
```

- [ ] **Step 2: Run the existing test suite to confirm no regressions**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASSED (UI has no unit tests — visual inspection needed separately)

- [ ] **Step 3: Commit**

```bash
git add admin/thunder_admin/pages/models.py
git commit -m "feat: add server tuning expander to edit model form"
```

---

### Task 4: Admin UI — Server Tuning expander in Add Model form

**Files:**
- Modify: `admin/thunder_admin/pages/models.py`

The Add Model confirmation form lives inside `with st.form("confirm_model"):` (lines 188-232). We add the same expander after the `notes` field and before the submit buttons.

- [ ] **Step 1: Add the Server Tuning expander to the Add Model form**

Replace the `with st.form("confirm_model"):` block (lines 188-232) with:

```python
        with st.form("confirm_model"):
            st.write(f"**Repo:** {pending_repo}")
            disk_gb = st.number_input("Disk GB", value=meta["disk_gb"], step=0.1)
            ram_gb = st.number_input("RAM GB override (0 = auto)", value=0.0, step=0.1)
            max_context = st.number_input("Max context", value=meta["max_context"], step=1024)
            kv_per_32k_gb = st.number_input("KV per 32k GB", value=meta["kv_per_32k_gb"], step=0.01)
            revision = st.text_input("Revision", value=meta["revision"])
            serving = st.selectbox("Serving", ["", "embedding", "cli", "mlx-openai-server"])
            thinking_label = st.selectbox("Thinking mode", ["Default", "Enabled", "Disabled"])
            notes = st.text_area("Notes")

            with st.expander("Server Tuning (Advanced)", expanded=False):
                st.caption("Leave blank to use mlx_lm.server defaults.")
                c1, c2, c3 = st.columns(3)
                add_decode_concurrency = c1.number_input(
                    "Decode concurrency", value=0, min_value=0, step=1,
                    help="mlx default: 32. 0 = use default.", key="add_sa_decode",
                )
                add_prompt_concurrency = c2.number_input(
                    "Prompt concurrency", value=0, min_value=0, step=1,
                    help="mlx default: 8. 0 = use default.", key="add_sa_prompt",
                )
                add_prefill_step = c3.number_input(
                    "Prefill step size", value=0, min_value=0, step=256,
                    help="mlx default: 2048. 0 = use default.", key="add_sa_prefill",
                )
                c4, c5 = st.columns(2)
                add_cache_size = c4.number_input(
                    "Prompt cache size", value=0, min_value=0, step=1,
                    help="KV cache entry count. 0 = use default.", key="add_sa_cache_size",
                )
                add_cache_bytes = c5.number_input(
                    "Prompt cache bytes", value=0, min_value=0, step=1073741824,
                    help="KV cache size in bytes. 0 = use default.", key="add_sa_cache_bytes",
                )
                st.markdown("**Sampling defaults**")
                c6, c7, c8, c9, c10 = st.columns(5)
                add_max_tokens = c6.number_input(
                    "Max tokens", value=0, min_value=0, step=256,
                    help="mlx default: 512. 0 = use default.", key="add_sa_max_tokens",
                )
                add_temp = c7.number_input(
                    "Temp", value=0.0, min_value=0.0, max_value=2.0, step=0.05,
                    help="mlx default: 0.0", key="add_sa_temp",
                )
                add_top_p = c8.number_input(
                    "Top-p", value=0.0, min_value=0.0, max_value=1.0, step=0.05,
                    help="mlx default: 1.0. 0 = use default.", key="add_sa_top_p",
                )
                add_top_k = c9.number_input(
                    "Top-k", value=0, min_value=0, step=1,
                    help="mlx default: 0 (disabled)", key="add_sa_top_k",
                )
                add_min_p = c10.number_input(
                    "Min-p", value=0.0, min_value=0.0, max_value=1.0, step=0.01,
                    help="mlx default: 0.0 (disabled)", key="add_sa_min_p",
                )
                st.markdown("**Speculative decoding**")
                c11, c12 = st.columns(2)
                add_draft_model = c11.text_input(
                    "Draft model", value="",
                    help="HF repo or local path for speculative decoding draft model.",
                    key="add_sa_draft_model",
                )
                add_num_draft_tokens = c12.number_input(
                    "Num draft tokens", value=0, min_value=0, step=1,
                    help="mlx default: 3. 0 = use default.", key="add_sa_num_draft",
                )
                add_extra_args_text = st.text_area(
                    "Extra args (one flag per line)",
                    value="",
                    help="Arbitrary mlx_lm.server flags. Appended after all named args above.",
                    key="add_sa_extra",
                )

            col_add, col_cancel = st.columns(2)

            if col_add.form_submit_button("Add Model"):
                if not pending_name:
                    st.error("Model name is required")
                elif pending_name in models:
                    st.error(f"Model '{pending_name}' already exists")
                else:
                    new_thinking = {"Default": None, "Enabled": True, "Disabled": False}[thinking_label]

                    # Build server_args dict
                    new_sa = {}
                    if add_decode_concurrency > 0:
                        new_sa["decode_concurrency"] = add_decode_concurrency
                    if add_prompt_concurrency > 0:
                        new_sa["prompt_concurrency"] = add_prompt_concurrency
                    if add_prefill_step > 0:
                        new_sa["prefill_step_size"] = add_prefill_step
                    if add_cache_size > 0:
                        new_sa["prompt_cache_size"] = add_cache_size
                    if add_cache_bytes > 0:
                        new_sa["prompt_cache_bytes"] = add_cache_bytes
                    if add_max_tokens > 0:
                        new_sa["max_tokens"] = add_max_tokens
                    if add_temp > 0.0:
                        new_sa["temp"] = add_temp
                    if add_top_p > 0.0:
                        new_sa["top_p"] = add_top_p
                    if add_top_k > 0:
                        new_sa["top_k"] = add_top_k
                    if add_min_p > 0.0:
                        new_sa["min_p"] = add_min_p
                    if add_draft_model.strip():
                        new_sa["draft_model"] = add_draft_model.strip()
                    if add_num_draft_tokens > 0:
                        new_sa["num_draft_tokens"] = add_num_draft_tokens

                    parsed_extra = [l for l in add_extra_args_text.splitlines() if l.strip()]

                    new_model = {
                        "source": {
                            "type": "huggingface",
                            "repo": pending_repo,
                            "revision": revision,
                        },
                        "disk_gb": disk_gb,
                        "kv_per_32k_gb": kv_per_32k_gb,
                        "max_context": max_context,
                        "server_args": new_sa if new_sa else None,
                        "extra_args": parsed_extra if parsed_extra else None,
                        "serving": serving,
                        "notes": notes,
                    }
                    if new_thinking is not None:
                        new_model["enable_thinking"] = new_thinking
                    if ram_gb > 0:
                        new_model["ram_gb"] = ram_gb
                    config["models"][pending_name] = new_model
                    if save_config_or_error(st, config, user, f"Added model '{pending_name}'"):
                        del st.session_state["hf_pending"]
                        st.success(f"Added '{pending_name}'")
                        st.rerun()

            if col_cancel.form_submit_button("Cancel"):
                del st.session_state["hf_pending"]
                st.rerun()
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASSED

- [ ] **Step 3: Run linter**

```bash
uv run ruff check admin/ src/ tests/
```

Expected: no errors. If line-length violations appear, run:

```bash
uv run ruff format admin/ src/ tests/
```

- [ ] **Step 4: Commit**

```bash
git add admin/thunder_admin/pages/models.py
git commit -m "feat: add server tuning expander to add model form"
```
