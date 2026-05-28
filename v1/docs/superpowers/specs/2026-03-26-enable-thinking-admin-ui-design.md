# Enable Thinking — Admin UI Design

**Date:** 2026-03-26
**Scope:** `admin/thunder_admin/pages/models.py` only

## Background

`enable_thinking` was added as a per-model config param (`bool | None`) in the `Model` dataclass and `node-assignments.yaml`. When set, `generate_plist` injects `--chat-template-args '{"enable_thinking": ...}'` into the mlx_lm.server launch command. The admin UI does not yet expose this field.

## Goal

Make `enable_thinking` visible and editable per model in the Models page, using a tri-state selectbox (`Default` / `Enabled` / `Disabled`).

## Value mapping

| Selectbox label | Python value | YAML / JSON |
|---|---|---|
| Default | `None` | key omitted |
| Enabled | `True` | `true` |
| Disabled | `False` | `false` |

When saving, omit the key entirely (or set to `None`) when the user selects "Default", to keep YAML clean.

## Changes — `pages/models.py`

### 1. Model card (read view)

Add a third column to the second metadata row alongside KV/32k and Serving:

```
KV/32k: 0.3 GB   Serving: mlx_lm.server   Thinking: Disabled
```

Display logic:
```python
thinking_val = model.get("enable_thinking")  # None, True, or False
thinking_label = {True: "Enabled", False: "Disabled"}.get(thinking_val, "Default")
col6.write(f"**Thinking:** {thinking_label}")
```

### 2. Edit form

Add one selectbox after the `serving` field:

```python
_THINKING_OPTIONS = ["Default", "Enabled", "Disabled"]
_THINKING_FROM_VAL = {None: 0, True: 1, False: 2}

current_thinking = model.get("enable_thinking")
new_thinking_label = st.selectbox(
    "Thinking mode",
    _THINKING_OPTIONS,
    index=_THINKING_FROM_VAL.get(current_thinking, 0),
    key=f"edit_thinking_{name}",
)
```

On save:
```python
_THINKING_TO_VAL = {"Default": None, "Enabled": True, "Disabled": False}
new_thinking = _THINKING_TO_VAL[new_thinking_label]
if new_thinking is None:
    model.pop("enable_thinking", None)
else:
    model["enable_thinking"] = new_thinking
```

### 3. Add model form (`confirm_model`)

Same selectbox, defaulting to `"Default"`. Same save mapping applied to `new_model` dict before inserting into config.

## Out of scope

- No changes to `config.py`, `deploy.py`, or the `Model` dataclass — already correct.
- No filtering by model family (applies to all models).
- No per-request thinking control in the UI (that's a client-side concern).
