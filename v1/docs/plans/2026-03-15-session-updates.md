# Session Updates — 2026-03-15

Tasks completed during this session, covering model registry sync, setup script improvements, documentation, config corrections, and deployment fixes.

---

## Task 1: Sync model registry with knowledge base

**Commits:** `8f83167`

**Problem:** `configs/node-assignments.yaml` only contained the `coder` model with incorrect KV cache and context values. The Obsidian knowledge base had been updated with new models and corrected specs.

**Changes:**
- [x] Fix `coder` model: `kv_per_32k_gb` 8 → 0.75 (Gated DeltaNet hybrid attention), `max_context` 131072 → 262144
- [x] Add `general` model (Qwen3.5-35B-A3B-4bit, 20.4 GB, multimodal MoE)
- [x] Add `fast` model (Qwen3.5-9B-MLX-4bit, 5.6 GB, dense)
- [x] Add `embedding` model (Qwen3-Embedding-0.6B-4bit-DWQ, serving: embedding)
- [x] Add `image-gen` model (Z-Image-Turbo via mflux, serving: mlx-openai-server)
- [x] Add `video-gen` model (LTX-2 distilled via mlx-video, serving: cli)
- [x] Add retired model comments (gpt-oss, Qwen3-Next-80B-A3B)
- [x] Add source type comments to registry header

**Files:** `configs/node-assignments.yaml`

---

## Task 2: Make setup-node.sh paths configurable

**Commits:** `de47fb4`, `2db6645`

**Problem:** All paths in `scripts/setup-node.sh` were hardcoded (`~/thunder-forge`, `~/logs`, `~/.ssh/id_ed25519`, clone URL), making it inflexible for non-default deployments.

**Changes:**
- [x] Extract hardcoded paths into env vars with defaults: `TF_DIR`, `TF_LOG_DIR`, `TF_SSH_KEY`, `TF_REPO_URL`
- [x] Add `.env` file loading (checks `scripts/.env` then `~/.thunder-forge.env`)
- [x] Env vars take precedence over `.env` file values
- [x] Document all configurable variables in script header
- [x] Expand `~` to `$HOME` in .env-sourced paths (tilde is literal in non-interactive reads)

**Files:** `scripts/setup-node.sh`

---

## Task 3: Create cluster setup guide

**Commits:** `1eadec3`

**Problem:** No user-facing documentation for end-to-end cluster deployment.

**Changes:**
- [x] Write `docs/setup-guide.md` covering full deployment flow
- [x] Document cluster overview (nodes, IPs, roles)
- [x] Step-by-step: bootstrap infra → bootstrap inference → SSH keys → config → models → generate-config → Docker → deploy → health
- [x] Include custom path configuration examples
- [x] Add troubleshooting section (logs, Docker, memory, re-deploy)
- [x] Document service access (LiteLLM API, Open WebUI) with curl example

**Files:** `docs/setup-guide.md`

---

## Task 4: Fix rock user and shell defaults

**Commits:** `59c3fc9`

**Problem:** Rock's SSH user was incorrectly set to `admin` (actual: `infra_user`). The setup script had a `.bashrc` fallback despite zsh being the default shell on all nodes.

**Changes:**
- [x] Update rock user from `admin` to `infra_user` in `configs/node-assignments.yaml`
- [x] Update all test fixtures (4 test files) to use `infra_user` for rock
- [x] Update `docs/setup-guide.md` SSH examples to use `infra_user@192.168.1.61`
- [x] Update `scripts/setup-node.sh` ssh-copy-id output to use `$USER` instead of hardcoded `admin`
- [x] Remove `.bashrc` fallback in infra setup — always write to `~/.zshrc`

**Files:** `configs/node-assignments.yaml`, `scripts/setup-node.sh`, `docs/setup-guide.md`, `tests/test_config.py`, `tests/test_health.py`, `tests/test_deploy.py`, `tests/test_models.py`

---

## Task 5: Make SSH user configurable with role-based defaults

**Commits:** `9794eb6`, `57256af`, `3e49264`

**Problem:** SSH user was hardcoded per-node in YAML. Needed to be configurable via env var with sensible defaults that differ by node role.

**Changes:**
- [x] User resolution: YAML `user` field > `TF_SSH_USER` env var > role-based default
- [x] Infra nodes (rock) default to current OS user (`os.getlogin()`)
- [x] Inference nodes default to `"admin"`
- [x] Remove explicit user from `node-assignments.yaml` (defaults apply)
- [x] Add tests for default user resolution and env var override

**Files:** `src/thunder_forge/cluster/config.py`, `configs/node-assignments.yaml`, `tests/test_config.py`

---

## Task 6: Skip SSH when target is the local machine

**Commits:** `0c4aa14`, `4974086`

**Problem:** Running `thunder-forge ensure-models` from rock prompted for a password because the CLI was SSHing from rock to rock (itself) to download models.

**Changes:**
- [x] Add `_is_local(ip)` helper to `ssh.py` — uses socket bind to reliably detect local IPs
- [x] `ssh_run` runs commands locally via `bash -lc` (login shell for PATH) when target is local
- [x] `scp_content` writes directly when target is local
- [x] `ensure_huggingface` uses local rsync source path when rock is local

**Files:** `src/thunder_forge/cluster/ssh.py`, `src/thunder_forge/cluster/models.py`

---

## Task 7: Download pip model weights on rock, rsync to nodes

**Commits:** `877b6c7`

**Problem:** `ensure_pip` ran `huggingface-cli download` directly on inference nodes, requiring `huggingface-cli` to be installed there.

**Changes:**
- [x] Refactor `ensure_pip` to download weights on rock first, then rsync to target nodes
- [x] Inference nodes no longer need `huggingface-cli` or outbound internet access

**Files:** `src/thunder_forge/cluster/models.py`

---

## Task 8: Add HuggingFace CLI, auth, and proxy checks to infra setup

**Commits:** `e463c6c`, `f8ce58b`, `bf23b38`, `1a485ee`, `1e74a28`

**Problem:** Setup script didn't install the HF CLI, verify HF auth, or check that proxy env vars are set (required for outbound access through firewall).

**Changes:**
- [x] Install `hf` CLI via `uv tool install huggingface_hub` on rock
- [x] Warn if `hf auth whoami` fails (not authenticated)
- [x] Warn if `HTTP_PROXY`/`HTTPS_PROXY` not set
- [x] Use `hf` (modern CLI) instead of deprecated `huggingface-cli`
- [x] Create SSH key parent dir before keygen, skip if key already exists
- [x] Update `docs/setup-guide.md` with new steps and proxy note

**Files:** `scripts/setup-node.sh`, `docs/setup-guide.md`

---

## Task 9: Fix SOCKS proxy and HF download issues

**Commits:** `27f1979`, `b775596`

**Problem:** `hf download` failed because `httpx` picked up `ALL_PROXY` (SOCKS) over `HTTPS_PROXY` (HTTP). Also, download progress was invisible since output was captured.

**Changes:**
- [x] Prefix `hf` commands with `ALL_PROXY=` to force HTTP proxy usage
- [x] Add `stream` option to `ssh_run` — downloads now show progress in terminal
- [x] Support `HF_HOME` env var for custom cache location (rock has limited main disk)
- [x] Pass `HF_HOME` to all `hf download` commands
- [x] Replace hardcoded `~/.cache/huggingface/hub` with configurable `HF_CACHE`

**Files:** `src/thunder_forge/cluster/ssh.py`, `src/thunder_forge/cluster/models.py`, `scripts/setup-node.sh`

---

## Other commits (not from this session)

- `dd659cf` — `.env` added to `.gitignore` (pushed externally between our commits)
