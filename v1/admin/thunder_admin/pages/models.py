# admin/thunder_admin/pages/models.py
"""Models config page — CRUD with HuggingFace auto-fill."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.config import save_config_or_error
from thunder_admin.hf import fetch_config_json, fetch_model_info, parse_model_metadata


def render(user: dict):
    st.header("Models")

    current = db.get_current_config()
    if current:
        config = current["config"]
        st.session_state.setdefault("loaded_config_id", current["id"])
    else:
        config = {
            "models": {},
            "nodes": {},
            "assignments": {},
            "external_endpoints": [],
        }
        st.session_state.setdefault("loaded_config_id", None)

    models = config.get("models", {})

    # Models table
    if models:
        for name, model in list(models.items()):
            source = model.get("source", {})
            with st.expander(f"**{name}** — {source.get('repo', 'N/A')}", expanded=False):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Disk:** {model.get('disk_gb', 0)} GB")
                col2.write(f"**RAM override:** {model.get('ram_gb', 'auto')}")
                col3.write(f"**Max context:** {model.get('max_context', 0):,}")

                col4, col5, col6 = st.columns(3)
                col4.write(f"**KV/32k:** {model.get('kv_per_32k_gb', 0)} GB")
                col5.write(f"**Serving:** {model.get('serving', '') or 'mlx_lm.server'}")
                thinking_label = {True: "Enabled", False: "Disabled"}.get(model.get("enable_thinking"), "Default")
                col6.write(f"**Thinking:** {thinking_label}")

                if model.get("notes"):
                    st.caption(model["notes"])

                btn_col1, btn_col2 = st.columns(2)

                # Edit model
                if btn_col1.button("Edit", key=f"edit_{name}"):
                    st.session_state[f"editing_model_{name}"] = True
                    st.rerun()

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

                        with st.expander("LiteLLM Routing (Advanced)", expanded=False):
                            lp = model.get("litellm_params") or {}
                            st.caption(
                                "Controls how LiteLLM proxy routes requests to this model. 0 = use global default."
                            )
                            lp_c1, lp_c2, lp_c3 = st.columns(3)
                            new_lp_max_output = lp_c1.number_input(
                                "Max output tokens",
                                value=int(lp.get("max_output_tokens") or 0),
                                min_value=0,
                                step=1024,
                                help="Tokens LiteLLM allows in response. Default: 16384. 0 = use default.",
                                key=f"edit_lp_max_out_{name}",
                            )
                            new_lp_timeout = lp_c2.number_input(
                                "Timeout (s)",
                                value=int(lp.get("timeout") or 0),
                                min_value=0,
                                step=10,
                                help="Per-model request timeout in seconds. 0 = use global (120s).",
                                key=f"edit_lp_timeout_{name}",
                            )
                            new_lp_stream_timeout = lp_c3.number_input(
                                "Stream timeout (s)",
                                value=int(lp.get("stream_timeout") or 0),
                                min_value=0,
                                step=10,
                                help="Per-model streaming timeout in seconds. 0 = inherit global.",
                                key=f"edit_lp_stream_timeout_{name}",
                            )
                            lp_c4, lp_c5, lp_c6 = st.columns(3)
                            new_lp_weight = lp_c4.number_input(
                                "Weight",
                                value=int(lp.get("weight") or 0),
                                min_value=0,
                                step=1,
                                help="Load-balancing weight across nodes serving this model. 0 = default (1).",
                                key=f"edit_lp_weight_{name}",
                            )
                            new_lp_tpm = lp_c5.number_input(
                                "TPM limit",
                                value=int(lp.get("tpm") or 0),
                                min_value=0,
                                step=1000,
                                help="Tokens per minute rate limit. 0 = unlimited.",
                                key=f"edit_lp_tpm_{name}",
                            )
                            new_lp_rpm = lp_c6.number_input(
                                "RPM limit",
                                value=int(lp.get("rpm") or 0),
                                min_value=0,
                                step=10,
                                help="Requests per minute rate limit. 0 = unlimited.",
                                key=f"edit_lp_rpm_{name}",
                            )
                            lp_c7, lp_c8, lp_c9 = st.columns(3)
                            new_lp_temperature = lp_c7.number_input(
                                "Temperature",
                                value=float(lp.get("temperature") or 0.0),
                                min_value=0.0,
                                max_value=2.0,
                                step=0.05,
                                help="Proxy-level default temperature. 0 = model default.",
                                key=f"edit_lp_temp_{name}",
                            )
                            new_lp_max_tokens = lp_c8.number_input(
                                "Max tokens",
                                value=int(lp.get("max_tokens") or 0),
                                min_value=0,
                                step=256,
                                help="Proxy-level default max_tokens for completions. 0 = model default.",
                                key=f"edit_lp_max_tokens_{name}",
                            )
                            new_lp_seed = lp_c9.number_input(
                                "Seed",
                                value=int(lp.get("seed") or 0),
                                min_value=0,
                                step=1,
                                help="Seed for reproducible outputs. 0 = non-deterministic.",
                                key=f"edit_lp_seed_{name}",
                            )

                        with st.expander("Model Capabilities & Costs (Advanced)", expanded=False):
                            mi = model.get("model_info") or {}
                            st.caption(
                                "Metadata for LiteLLM routing decisions, cost tracking, and capability detection."
                            )
                            mi_c1, mi_c2 = st.columns(2)
                            new_mi_base_model = mi_c1.text_input(
                                "Base model",
                                value=mi.get("base_model") or "",
                                help="Maps custom names to known models for token counting/costs.",
                                key=f"edit_mi_base_model_{name}",
                            )
                            _MODE_OPTIONS = ["", "chat", "completion", "embedding", "image_generation"]
                            cur_mode = mi.get("mode", "")
                            new_mi_mode = mi_c2.selectbox(
                                "Mode",
                                _MODE_OPTIONS,
                                index=_MODE_OPTIONS.index(cur_mode) if cur_mode in _MODE_OPTIONS else 0,
                                help="Model mode for routing. Empty = auto-detect.",
                                key=f"edit_mi_mode_{name}",
                            )
                            mi_c3, mi_c4 = st.columns(2)
                            new_mi_input_cost = mi_c3.number_input(
                                "Input cost per token",
                                value=float(mi.get("input_cost_per_token") or 0.0),
                                min_value=0.0,
                                step=0.000001,
                                format="%.6f",
                                help="Cost per input token for budget tracking. 0 = free/unknown.",
                                key=f"edit_mi_input_cost_{name}",
                            )
                            new_mi_output_cost = mi_c4.number_input(
                                "Output cost per token",
                                value=float(mi.get("output_cost_per_token") or 0.0),
                                min_value=0.0,
                                step=0.000001,
                                format="%.6f",
                                help="Cost per output token for budget tracking. 0 = free/unknown.",
                                key=f"edit_mi_output_cost_{name}",
                            )
                            st.markdown("**Capability flags**")
                            mi_c5, mi_c6, mi_c7, mi_c8 = st.columns(4)
                            new_mi_vision = mi_c5.checkbox(
                                "Vision",
                                value=bool(mi.get("supports_vision")),
                                help="Supports image/multimodal inputs.",
                                key=f"edit_mi_vision_{name}",
                            )
                            new_mi_fc = mi_c6.checkbox(
                                "Function calling",
                                value=bool(mi.get("supports_function_calling")),
                                help="Supports tool/function calling.",
                                key=f"edit_mi_fc_{name}",
                            )
                            new_mi_pfc = mi_c7.checkbox(
                                "Parallel FC",
                                value=bool(mi.get("supports_parallel_function_calling")),
                                help="Supports parallel function calling.",
                                key=f"edit_mi_pfc_{name}",
                            )
                            new_mi_schema = mi_c8.checkbox(
                                "Response schema",
                                value=bool(mi.get("supports_response_schema")),
                                help="Supports structured output / response schema.",
                                key=f"edit_mi_schema_{name}",
                            )

                        with st.expander("Server Tuning (Advanced)", expanded=False):
                            sa = model.get("server_args") or {}
                            st.caption("Leave blank to use mlx_lm.server defaults.")
                            c1, c2, c3 = st.columns(3)
                            new_decode_concurrency = c1.number_input(
                                "Decode concurrency",
                                value=int(sa.get("decode_concurrency") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 32. 0 = use default.",
                                key=f"edit_sa_decode_{name}",
                            )
                            new_prompt_concurrency = c2.number_input(
                                "Prompt concurrency",
                                value=int(sa.get("prompt_concurrency") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 8. 0 = use default.",
                                key=f"edit_sa_prompt_{name}",
                            )
                            new_prefill_step = c3.number_input(
                                "Prefill step size",
                                value=int(sa.get("prefill_step_size") or 0),
                                min_value=0,
                                step=256,
                                help="mlx default: 2048. 0 = use default.",
                                key=f"edit_sa_prefill_{name}",
                            )
                            c4, c5 = st.columns(2)
                            new_cache_size = c4.number_input(
                                "Prompt cache size",
                                value=int(sa.get("prompt_cache_size") or 0),
                                min_value=0,
                                step=1,
                                help="KV cache entry count. 0 = use default.",
                                key=f"edit_sa_cache_size_{name}",
                            )
                            new_cache_bytes = c5.number_input(
                                "Prompt cache bytes",
                                value=int(sa.get("prompt_cache_bytes") or 0),
                                min_value=0,
                                step=1073741824,
                                help="KV cache size in bytes. 0 = use default.",
                                key=f"edit_sa_cache_bytes_{name}",
                            )
                            st.markdown("**Sampling defaults**")
                            c6, c7, c8, c9, c10 = st.columns(5)
                            new_max_tokens = c6.number_input(
                                "Max tokens",
                                value=int(sa.get("max_tokens") or 0),
                                min_value=0,
                                step=256,
                                help="mlx default: 512. 0 = use default.",
                                key=f"edit_sa_max_tokens_{name}",
                            )
                            new_temp = c7.number_input(
                                "Temp",
                                value=float(sa.get("temp") or 0.0),
                                min_value=0.0,
                                max_value=2.0,
                                step=0.05,
                                help="mlx default: 0.0",
                                key=f"edit_sa_temp_{name}",
                            )
                            new_top_p = c8.number_input(
                                "Top-p",
                                value=float(sa.get("top_p") or 0.0),
                                min_value=0.0,
                                max_value=1.0,
                                step=0.05,
                                help="mlx default: 1.0. 0 = use default.",
                                key=f"edit_sa_top_p_{name}",
                            )
                            new_top_k = c9.number_input(
                                "Top-k",
                                value=int(sa.get("top_k") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 0 (disabled)",
                                key=f"edit_sa_top_k_{name}",
                            )
                            new_min_p = c10.number_input(
                                "Min-p",
                                value=float(sa.get("min_p") or 0.0),
                                min_value=0.0,
                                max_value=1.0,
                                step=0.01,
                                help="mlx default: 0.0 (disabled)",
                                key=f"edit_sa_min_p_{name}",
                            )
                            st.markdown("**Speculative decoding**")
                            c11, c12 = st.columns(2)
                            new_draft_model = c11.text_input(
                                "Draft model",
                                value=sa.get("draft_model") or "",
                                help="HF repo or local path for speculative decoding draft model.",
                                key=f"edit_sa_draft_model_{name}",
                            )
                            new_num_draft_tokens = c12.number_input(
                                "Num draft tokens",
                                value=int(sa.get("num_draft_tokens") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 3. 0 = use default.",
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

                            # Build litellm_params dict — only non-zero values
                            new_lp = {}
                            if new_lp_max_output > 0:
                                new_lp["max_output_tokens"] = new_lp_max_output
                            if new_lp_timeout > 0:
                                new_lp["timeout"] = new_lp_timeout
                            if new_lp_stream_timeout > 0:
                                new_lp["stream_timeout"] = new_lp_stream_timeout
                            if new_lp_weight > 0:
                                new_lp["weight"] = new_lp_weight
                            if new_lp_tpm > 0:
                                new_lp["tpm"] = new_lp_tpm
                            if new_lp_rpm > 0:
                                new_lp["rpm"] = new_lp_rpm
                            if new_lp_temperature > 0.0:
                                new_lp["temperature"] = new_lp_temperature
                            if new_lp_max_tokens > 0:
                                new_lp["max_tokens"] = new_lp_max_tokens
                            if new_lp_seed > 0:
                                new_lp["seed"] = new_lp_seed
                            if new_lp:
                                model["litellm_params"] = new_lp
                            else:
                                model.pop("litellm_params", None)

                            # Build model_info dict — only non-empty/non-zero values
                            new_mi = {}
                            if new_mi_base_model.strip():
                                new_mi["base_model"] = new_mi_base_model.strip()
                            if new_mi_mode:
                                new_mi["mode"] = new_mi_mode
                            if new_mi_input_cost > 0.0:
                                new_mi["input_cost_per_token"] = new_mi_input_cost
                            if new_mi_output_cost > 0.0:
                                new_mi["output_cost_per_token"] = new_mi_output_cost
                            if new_mi_vision:
                                new_mi["supports_vision"] = True
                            if new_mi_fc:
                                new_mi["supports_function_calling"] = True
                            if new_mi_pfc:
                                new_mi["supports_parallel_function_calling"] = True
                            if new_mi_schema:
                                new_mi["supports_response_schema"] = True
                            if new_mi:
                                model["model_info"] = new_mi
                            else:
                                model.pop("model_info", None)

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
                            if new_sa:
                                model["server_args"] = new_sa
                            else:
                                model.pop("server_args", None)

                            # Parse extra_args text area
                            parsed_extra = [line for line in new_extra_args_text.splitlines() if line.strip()]
                            model["extra_args"] = parsed_extra if parsed_extra else None

                            if save_config_or_error(st, config, user, f"Updated model '{name}'"):
                                del st.session_state[f"editing_model_{name}"]
                                st.success(f"Updated '{name}'")
                                st.rerun()
                        if col_cancel.form_submit_button("Cancel"):
                            del st.session_state[f"editing_model_{name}"]
                            st.rerun()

                if btn_col2.button("Delete", key=f"delete_{name}", type="secondary"):
                    # Check for assignments referencing this model
                    refs = []
                    for node_name, slots in config.get("assignments", {}).items():
                        for slot in slots:
                            if slot.get("model") == name:
                                refs.append(f"{node_name}:{slot.get('port', '?')}")
                    if refs:
                        st.warning(f"This model is assigned to: {', '.join(refs)}. Remove assignments and delete?")
                        if st.button("Confirm delete", key=f"confirm_delete_{name}"):
                            for node_name in list(config.get("assignments", {}).keys()):
                                config["assignments"][node_name] = [
                                    s for s in config["assignments"][node_name] if s.get("model") != name
                                ]
                            del config["models"][name]
                            if save_config_or_error(
                                st,
                                config,
                                user,
                                f"Deleted model '{name}' and its assignments",
                            ):
                                st.success(f"Deleted '{name}'")
                                st.rerun()
                    else:
                        del config["models"][name]
                        if save_config_or_error(st, config, user, f"Deleted model '{name}'"):
                            st.success(f"Deleted '{name}'")
                            st.rerun()
    else:
        st.info("No models configured. Add one below.")

    # Add model form
    st.subheader("Add Model")
    with st.form("add_model"):
        name = st.text_input("Model name (short identifier, e.g. 'coder')")
        repo = st.text_input("HuggingFace repo (e.g. mlx-community/Qwen3-Coder-Next-4bit)")
        auto_fill = st.form_submit_button("Fetch from HuggingFace")

    if auto_fill and repo:
        try:
            with st.spinner("Fetching model info..."):
                info = fetch_model_info(repo)
                config_json = fetch_config_json(repo) or {}
                meta = parse_model_metadata(info, config_json)
            st.session_state["hf_pending"] = {"name": name, "repo": repo, "meta": meta}
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg:
                st.error("This repo requires authentication. Set HF_TOKEN in docker-compose environment.")
            else:
                st.warning(f"HF API unavailable: {error_msg}. Enter values manually.")

    if st.session_state.get("hf_pending"):
        pending = st.session_state["hf_pending"]
        pending_name = pending["name"]
        pending_repo = pending["repo"]
        meta = pending["meta"]

        if not meta["has_safetensors"]:
            st.warning("Repo does not contain safetensors files.")
        if not meta["has_tokenizer"]:
            st.warning("Repo missing tokenizer_config.json (required by mlx_lm.server).")

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

            with st.expander("LiteLLM Routing (Advanced)", expanded=False):
                st.caption("Controls how LiteLLM proxy routes requests to this model. 0 = use global default.")
                lp_c1, lp_c2, lp_c3 = st.columns(3)
                add_lp_max_output = lp_c1.number_input(
                    "Max output tokens",
                    value=0,
                    min_value=0,
                    step=1024,
                    help="Tokens LiteLLM allows in response. Default: 16384. 0 = use default.",
                    key="add_lp_max_out",
                )
                add_lp_timeout = lp_c2.number_input(
                    "Timeout (s)",
                    value=0,
                    min_value=0,
                    step=10,
                    help="Per-model request timeout in seconds. 0 = use global (120s).",
                    key="add_lp_timeout",
                )
                add_lp_stream_timeout = lp_c3.number_input(
                    "Stream timeout (s)",
                    value=0,
                    min_value=0,
                    step=10,
                    help="Per-model streaming timeout in seconds. 0 = inherit global.",
                    key="add_lp_stream_timeout",
                )
                lp_c4, lp_c5, lp_c6 = st.columns(3)
                add_lp_weight = lp_c4.number_input(
                    "Weight",
                    value=0,
                    min_value=0,
                    step=1,
                    help="Load-balancing weight across nodes serving this model. 0 = default (1).",
                    key="add_lp_weight",
                )
                add_lp_tpm = lp_c5.number_input(
                    "TPM limit",
                    value=0,
                    min_value=0,
                    step=1000,
                    help="Tokens per minute rate limit. 0 = unlimited.",
                    key="add_lp_tpm",
                )
                add_lp_rpm = lp_c6.number_input(
                    "RPM limit",
                    value=0,
                    min_value=0,
                    step=10,
                    help="Requests per minute rate limit. 0 = unlimited.",
                    key="add_lp_rpm",
                )
                lp_c7, lp_c8, lp_c9 = st.columns(3)
                add_lp_temperature = lp_c7.number_input(
                    "Temperature",
                    value=0.0,
                    min_value=0.0,
                    max_value=2.0,
                    step=0.05,
                    help="Proxy-level default temperature. 0 = model default.",
                    key="add_lp_temp",
                )
                add_lp_max_tokens = lp_c8.number_input(
                    "Max tokens",
                    value=0,
                    min_value=0,
                    step=256,
                    help="Proxy-level default max_tokens for completions. 0 = model default.",
                    key="add_lp_max_tokens",
                )
                add_lp_seed = lp_c9.number_input(
                    "Seed",
                    value=0,
                    min_value=0,
                    step=1,
                    help="Seed for reproducible outputs. 0 = non-deterministic.",
                    key="add_lp_seed",
                )

            with st.expander("Model Capabilities & Costs (Advanced)", expanded=False):
                st.caption("Metadata for LiteLLM routing decisions, cost tracking, and capability detection.")
                mi_c1, mi_c2 = st.columns(2)
                add_mi_base_model = mi_c1.text_input(
                    "Base model",
                    value="",
                    help="Maps custom names to known models for token counting/costs (e.g. 'meta-llama/Llama-3-70b').",
                    key="add_mi_base_model",
                )
                _ADD_MODE_OPTIONS = ["", "chat", "completion", "embedding", "image_generation"]
                add_mi_mode = mi_c2.selectbox(
                    "Mode",
                    _ADD_MODE_OPTIONS,
                    help="Model mode for routing. Empty = auto-detect.",
                    key="add_mi_mode",
                )
                mi_c3, mi_c4 = st.columns(2)
                add_mi_input_cost = mi_c3.number_input(
                    "Input cost per token",
                    value=0.0,
                    min_value=0.0,
                    step=0.000001,
                    format="%.6f",
                    help="Cost per input token for budget tracking. 0 = free/unknown.",
                    key="add_mi_input_cost",
                )
                add_mi_output_cost = mi_c4.number_input(
                    "Output cost per token",
                    value=0.0,
                    min_value=0.0,
                    step=0.000001,
                    format="%.6f",
                    help="Cost per output token for budget tracking. 0 = free/unknown.",
                    key="add_mi_output_cost",
                )
                st.markdown("**Capability flags**")
                mi_c5, mi_c6, mi_c7, mi_c8 = st.columns(4)
                add_mi_vision = mi_c5.checkbox(
                    "Vision",
                    help="Supports image/multimodal inputs.",
                    key="add_mi_vision",
                )
                add_mi_fc = mi_c6.checkbox(
                    "Function calling",
                    help="Supports tool/function calling.",
                    key="add_mi_fc",
                )
                add_mi_pfc = mi_c7.checkbox(
                    "Parallel FC",
                    help="Supports parallel function calling.",
                    key="add_mi_pfc",
                )
                add_mi_schema = mi_c8.checkbox(
                    "Response schema",
                    help="Supports structured output / response schema.",
                    key="add_mi_schema",
                )

            with st.expander("Server Tuning (Advanced)", expanded=False):
                st.caption("Leave blank to use mlx_lm.server defaults.")
                c1, c2, c3 = st.columns(3)
                add_decode_concurrency = c1.number_input(
                    "Decode concurrency",
                    value=0,
                    min_value=0,
                    step=1,
                    help="mlx default: 32. 0 = use default.",
                    key="add_sa_decode",
                )
                add_prompt_concurrency = c2.number_input(
                    "Prompt concurrency",
                    value=0,
                    min_value=0,
                    step=1,
                    help="mlx default: 8. 0 = use default.",
                    key="add_sa_prompt",
                )
                add_prefill_step = c3.number_input(
                    "Prefill step size",
                    value=0,
                    min_value=0,
                    step=256,
                    help="mlx default: 2048. 0 = use default.",
                    key="add_sa_prefill",
                )
                c4, c5 = st.columns(2)
                add_cache_size = c4.number_input(
                    "Prompt cache size",
                    value=0,
                    min_value=0,
                    step=1,
                    help="KV cache entry count. 0 = use default.",
                    key="add_sa_cache_size",
                )
                add_cache_bytes = c5.number_input(
                    "Prompt cache bytes",
                    value=0,
                    min_value=0,
                    step=1073741824,
                    help="KV cache size in bytes. 0 = use default.",
                    key="add_sa_cache_bytes",
                )
                st.markdown("**Sampling defaults**")
                c6, c7, c8, c9, c10 = st.columns(5)
                add_max_tokens = c6.number_input(
                    "Max tokens",
                    value=0,
                    min_value=0,
                    step=256,
                    help="mlx default: 512. 0 = use default.",
                    key="add_sa_max_tokens",
                )
                add_temp = c7.number_input(
                    "Temp",
                    value=0.0,
                    min_value=0.0,
                    max_value=2.0,
                    step=0.05,
                    help="mlx default: 0.0",
                    key="add_sa_temp",
                )
                add_top_p = c8.number_input(
                    "Top-p",
                    value=0.0,
                    min_value=0.0,
                    max_value=1.0,
                    step=0.05,
                    help="mlx default: 1.0. 0 = use default.",
                    key="add_sa_top_p",
                )
                add_top_k = c9.number_input(
                    "Top-k",
                    value=0,
                    min_value=0,
                    step=1,
                    help="mlx default: 0 (disabled)",
                    key="add_sa_top_k",
                )
                add_min_p = c10.number_input(
                    "Min-p",
                    value=0.0,
                    min_value=0.0,
                    max_value=1.0,
                    step=0.01,
                    help="mlx default: 0.0 (disabled)",
                    key="add_sa_min_p",
                )
                st.markdown("**Speculative decoding**")
                c11, c12 = st.columns(2)
                add_draft_model = c11.text_input(
                    "Draft model",
                    value="",
                    help="HF repo or local path for speculative decoding draft model.",
                    key="add_sa_draft_model",
                )
                add_num_draft_tokens = c12.number_input(
                    "Num draft tokens",
                    value=0,
                    min_value=0,
                    step=1,
                    help="mlx default: 3. 0 = use default.",
                    key="add_sa_num_draft",
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

                    # Build litellm_params dict
                    new_lp = {}
                    if add_lp_max_output > 0:
                        new_lp["max_output_tokens"] = add_lp_max_output
                    if add_lp_timeout > 0:
                        new_lp["timeout"] = add_lp_timeout
                    if add_lp_stream_timeout > 0:
                        new_lp["stream_timeout"] = add_lp_stream_timeout
                    if add_lp_weight > 0:
                        new_lp["weight"] = add_lp_weight
                    if add_lp_tpm > 0:
                        new_lp["tpm"] = add_lp_tpm
                    if add_lp_rpm > 0:
                        new_lp["rpm"] = add_lp_rpm
                    if add_lp_temperature > 0.0:
                        new_lp["temperature"] = add_lp_temperature
                    if add_lp_max_tokens > 0:
                        new_lp["max_tokens"] = add_lp_max_tokens
                    if add_lp_seed > 0:
                        new_lp["seed"] = add_lp_seed

                    # Build model_info dict
                    new_mi = {}
                    if add_mi_base_model.strip():
                        new_mi["base_model"] = add_mi_base_model.strip()
                    if add_mi_mode:
                        new_mi["mode"] = add_mi_mode
                    if add_mi_input_cost > 0.0:
                        new_mi["input_cost_per_token"] = add_mi_input_cost
                    if add_mi_output_cost > 0.0:
                        new_mi["output_cost_per_token"] = add_mi_output_cost
                    if add_mi_vision:
                        new_mi["supports_vision"] = True
                    if add_mi_fc:
                        new_mi["supports_function_calling"] = True
                    if add_mi_pfc:
                        new_mi["supports_parallel_function_calling"] = True
                    if add_mi_schema:
                        new_mi["supports_response_schema"] = True

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

                    parsed_extra = [line for line in add_extra_args_text.splitlines() if line.strip()]

                    new_model = {
                        "source": {
                            "type": "huggingface",
                            "repo": pending_repo,
                            "revision": revision,
                        },
                        "disk_gb": disk_gb,
                        "kv_per_32k_gb": kv_per_32k_gb,
                        "max_context": max_context,
                        "serving": serving,
                        "notes": notes,
                    }
                    if new_lp:
                        new_model["litellm_params"] = new_lp
                    if new_mi:
                        new_model["model_info"] = new_mi
                    if new_sa:
                        new_model["server_args"] = new_sa
                    if parsed_extra:
                        new_model["extra_args"] = parsed_extra
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
