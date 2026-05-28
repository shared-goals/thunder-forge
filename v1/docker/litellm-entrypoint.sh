#!/bin/sh
# Patches LiteLLM streaming bug where /v1/messages returns input_json_delta
# instead of text_delta for openai/ backend models.
#
# Bug: choice.delta.tool_calls=[] (empty list) is not None, so the adapter
# enters the tool_calls branch and emits input_json_delta with empty partial_json,
# discarding the actual text content.
#
# Affected: LiteLLM 1.81.x, 1.82.x. No upstream fix as of 2026-03-29.
# Remove this patch when LiteLLM fixes the bug upstream.

LITELLM_VERSION=$(python -c "
try:
    import litellm
    v = getattr(litellm, '__version__', None) or getattr(litellm, 'version', None)
    if v: print(v)
    else:
        from importlib.metadata import version
        print(version('litellm'))
except Exception:
    print('unknown')
" 2>/dev/null)

BUGGY_PATTERN='if choice.delta.tool_calls is not None:'
FIXED_PATTERN='if choice.delta.tool_calls is not None and len(choice.delta.tool_calls) > 0:'

patch_file() {
  if grep -q "$BUGGY_PATTERN" "$1"; then
    sed -i "s/$BUGGY_PATTERN/$FIXED_PATTERN/" "$1"
    echo "[litellm-entrypoint] Patched: $1"
    return 0
  fi
  return 1
}

# Patch versions known to be affected (1.81.x, 1.82.x)
case "$LITELLM_VERSION" in
  1.81.*|1.82.*)
    PATCHED=0
    for f in $(find / -path "*/anthropic/experimental_pass_through/adapters/transformation.py" 2>/dev/null); do
      patch_file "$f" && PATCHED=$((PATCHED + 1))
    done
    if [ "$PATCHED" -gt 0 ]; then
      echo "[litellm-entrypoint] Patched $PATCHED file(s) in LiteLLM $LITELLM_VERSION"
    else
      echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch already applied or pattern not found"
    fi
    ;;
  *)
    echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch not needed (only 1.81.x/1.82.x affected)"
    ;;
esac

exec "$@"
