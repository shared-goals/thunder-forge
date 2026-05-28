# msm3 artifact readiness and fabric inspection — 2026-05-26

## Scope

Development work was performed from `shag@studio` against `shag@msm3` only. Production `rock` and `msm4` were not touched.

## SSH host identity

`msm3-wifi.lan` was added to `~/.ssh/known_hosts` after verifying its scanned ed25519 host key matched the already-known `msm3-wifi` host key. No new SSH identity key was created; existing SSH config / `id_key` remains the identity source.

## oMLX product-state decision

TF v2/oMLX product state uses only the oMLX default model directory:

```text
~/.omlx/models/<model-dir>
```

oMLX documents that `omlx serve` discovers models as direct subdirectories of `~/.omlx/models` and uses the subdirectory name as the model id. For a Hugging Face repo id like `mlx-community/Qwen3-1.7B-4bit`, the selected local directory name is therefore:

```text
~/.omlx/models/Qwen3-1.7B-4bit
```

The old Hugging Face Hub cache layout (`~/.cache/huggingface/hub/models--.../snapshots/...`) is not modeled as TF v2 product state and should not get automated import/backfill logic in this MVP. New model downloads should target the oMLX model directory from the beginning.

## Initial state observations

At inspection time, neither studio nor `msm3` had `~/.omlx/models`. `msm3` did have several older Hugging Face cache entries from previous/manual work, but that state is historical evidence only and is not used by the TF v2 artifact planner.

Artifact status semantics after the `.omlx`-only refactor:

- missing on studio → next action `download_to_studio_omlx`;
- present on studio and missing on node → next action `sync_to_node_omlx`;
- present on studio and node → ready.

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

A small safe copy over the fabric path succeeded. This proves transport viability; durable config should enable dynamic probing with `fabric_host: true` rather than storing a fabric hostname or transient link-local literal.

## Follow-up design notes

- Keep `.lan` hostnames for homelab management configs.
- `fabric_host` is a boolean probe flag: true means probe Thunderbolt/fabric dynamically, false or absent means stay on management transport.
- Sync automation is a studio-to-node dry-run/apply CLI slice for oMLX model directories. The default auto path uses dynamic fabric only when `fabric_host: true`; management can still be forced explicitly.
- No node-to-studio backfill and no HF-cache-to-oMLX import should be implemented for the MVP product flow.
