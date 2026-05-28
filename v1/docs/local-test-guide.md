# Thunder Forge — Local Test Guide

> **For a full production setup, see [setup-guide.md](setup-guide.md).** This guide covers a minimal end-to-end test using two machines on a local network.

| Role | Machine | What it does |
|------|---------|-------------|
| Compute node | macOS Apple Silicon | Runs mlx-lm serving a small model |
| Gateway | Linux machine or VM | Docker Compose: LiteLLM + Open WebUI + PostgreSQL + Admin UI |

---

## Step 1: Bootstrap the compute node (macOS)

SSH into the Mac:

```bash
ssh <user>@<mac-ip>
```

On the Mac:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge

# Disable sleep warning for laptops
cat > .env <<'EOF'
TF_DISABLE_SLEEP=false
EOF

zsh scripts/setup-node.sh node
```

Expected output ends with:
```
=== Node setup complete ===
  Homebrew: ✓
  uv:       ✓
  mlx-lm:   ✓
  Logs:     ~/logs
```

Exit back to your workstation: `exit`

## Step 2: Bootstrap the gateway (Linux)

SSH into the Linux machine:

```bash
ssh <user>@<gateway-ip>
```

On the gateway:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge

cat > .env <<'EOF'
LITELLM_MASTER_KEY=test-key-local
POSTGRES_PASSWORD=localtest
WEBUI_SECRET_KEY=localtest-webui
ADMIN_DB_PASSWORD=localtest-admin
GATEWAY_SSH_USER=<your-username>
THUNDER_FORGE_DIR=~/thunder-forge
EOF

zsh scripts/setup-node.sh gateway
```

Expected output ends with:
```
=== Gateway setup complete ===
  Docker:   ✓
  uv:       ✓
  Compose:  running
```

Exit back to your workstation: `exit`

## Step 3: Distribute SSH key

From the gateway, copy its public key to the compute node:

```bash
ssh <gateway-user>@<gateway-ip> "cat ~/.ssh/id_ed25519.pub"
# Copy the output, then on the Mac:
ssh <mac-user>@<mac-ip> "echo '<public-key>' >> ~/.ssh/authorized_keys"
```

Verify:

```bash
ssh <gateway-user>@<gateway-ip> "ssh -i ~/.ssh/id_ed25519 <mac-user>@<mac-ip> echo ok"
```

## Step 4: Open Admin UI

Navigate to `http://<gateway-ip>:8501` and log in. Add your Mac as a compute node, add a small test model (e.g. `mlx-community/Qwen2.5-0.5B-Instruct-4bit`), assign it, and deploy.

## Step 5: Verify end-to-end

Once deployed, test via the LiteLLM proxy:

```bash
curl http://<gateway-ip>:4000/v1/chat/completions \
  -H "Authorization: Bearer test-key-local" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<your-model-name>",
    "messages": [{"role": "user", "content": "Say hello in 5 words"}],
    "max_tokens": 50
  }'
```

Open WebUI is available at `http://<gateway-ip>:8080`.

## Cleanup

Stop mlx-lm on the Mac:

```bash
ssh <mac-user>@<mac-ip> 'launchctl bootout gui/$(id -u)/com.mlx-lm-8000 2>/dev/null; rm ~/Library/LaunchAgents/com.mlx-lm-8000.plist'
```

Stop Docker on the gateway:

```bash
ssh <gateway-user>@<gateway-ip> "cd ~/thunder-forge && docker compose -f docker/docker-compose.yml --env-file .env down"
```

## Troubleshooting

**mlx-lm won't start:**
```bash
ssh <mac-user>@<mac-ip> "tail -50 ~/logs/mlx-lm-8000.err"
```

**LiteLLM can't reach compute node:**
```bash
ssh <gateway-user>@<gateway-ip> "curl -s http://<mac-ip>:8000/v1/models"
```

**Docker services unhealthy:**
```bash
ssh <gateway-user>@<gateway-ip> "cd ~/thunder-forge && docker compose -f docker/docker-compose.yml --env-file .env logs --tail=30"
```
