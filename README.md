# Thunder Forge

Infrastructure/process layer for running **self-hosted AI capabilities** to follow **Shared Goals**.

Thunder Forge focuses on operating the compute and automation stack that can work with **private data** (finance, healthcare, family/child privacy) without relying on third-party hosted agents.

- GitHub org: https://github.com/shared-goals/
- This repo: https://github.com/shared-goals/thunder-forge

## Shared Goals (idea)

A **personal image of Joy and Happiness** is treated as the base source of motives.

Details (RU): [Shared Goals — use case and concept](https://text.sharedgoals.ru/ru/p2-180-sharedgoals/#use_case)

- A *motive* is a reason to act (rooted in what brings joy/happiness).
- A *goal* is a direction or outcome shaped by one or more motives.
- When goals are **shared among coauthors**, motives combine and the overall dynamics increase.

## “Text” as a forkable source of goals

Shared Goals is developed as a living **Text** that anyone can fork and rewrite into their own.

- Concept Text (evolving): https://github.com/bongiozzo/whattodo
- Common build submodule: https://github.com/shared-goals/text-forge

`text-forge` transforms a Text repository into:

- a website (with link-sharing functionality in the publishing format)
- an EPUB book
- a combined Markdown corpus suitable for AI usage (RAG/MCP agents and skills)

## What Thunder Forge manages

Thunder Forge is the infrastructure/process layer for **self-hosted execution** of agents and skills.

Typical managed parts:

- **Nodes**: machines in a self-hosted cluster (e.g., several Mac Studios)
- **LLMs on nodes**: models served locally via Ollama (https://github.com/ollama/ollama)
- **Automation/agents runtime**: workflows and long-running processes (often via n8n)
  - n8n: https://github.com/n8n-io/n8n
- **Skills**: reusable tool capabilities agents can invoke
  - `github_repo` skills (agent skills): https://github.com/agentskills/agentskills

> Note: n8n and skills catalogs are intended integration points. This repo is the
> operational “glue” and runbooks/specs layer to run them self-hosted.

## Ecosystem map

```mermaid
flowchart LR
  SG["Shared Goals<br/>joy/happiness -> motives"] --> G["Goals<br/>(shared among coauthors)"]

  T["Text<br/>(forkable Markdown)"] --> TF["text-forge<br/>site + EPUB + combined corpus"]
  TF --> K["AI-ready corpus<br/>(RAG/MCP input)"]

  G --> A["Agents<br/>(plan and act)"]
  K --> A

  A --> S["Skills<br/>(callable capabilities)"]
  S --> N["Self-hosted nodes<br/>(cluster machines)"]

  N --> O["Local LLMs<br/>(Ollama)"]

  W["Orchestrator (optional)<br/>(n8n)"] --> A
  W --> S
```

## Privacy & self-hosting principles

Shared Goals activities can involve highly sensitive data.

- Prefer **self-hosted nodes** and **self-hosted agents** for private domains.
- Keep data access **least-privilege** (skills should request only what they need).
- Treat secrets and tokens as production-grade (no plaintext in repos).
- Make agent activity auditable (logs, runs, and permissions).

## Status

This repository is currently at an early scaffolding stage (see [LICENSE](LICENSE)).

Near-term intended contents include:

- node inventory/specs and bootstrap runbooks
- model/LLM deployment conventions (Ollama-managed)
- agent/workflow conventions (including optional n8n patterns)
- a skills registry format + examples

## Localhost Mini App (restricted, no DB)

This repo currently contains a minimal vertical slice:

- `GET /health` (public)
- `GET /mini-app/` (static Mini App)
- `POST /api/mini-app/me` (Telegram initData auth + admin allowlist)
- `POST /api/mini-app/status` (live reachability checks from inventory)

### Quickstart (localhost)

1. Create `tf.yml` (kept out of git; it’s ignored by default):

```yaml
server:
  bind: 127.0.0.1
  port: 8000
  reload: true

telegram:
  bot_token: "..."

access:
  admin_telegram_ids:
    - 123

mini_app_url: http://127.0.0.1:8000/mini-app/

settings:
  ssh:
    connect_timeout_seconds: 1.0
    batch_mode: true
  monitor:
    ssh_port: 22
    ollama_port: 11434
  hosts_sync:
    managed_block_start: "# BEGIN thunder-forge"
    managed_block_end: "# END thunder-forge"

nodes:
  - name: msm1
    ssh_user: you
    wifi_ip: 192.168.1.101
    service_manager: brew
    # tb_ip: 172.16.10.2
```

2. Run:

  - `make sync`
  - `make serve`

The server binds to `127.0.0.1:8000` by default.

### Notes

- This is intentionally **restricted**: if your Telegram user ID is not listed in `tf.yml` (`access.admin_telegram_ids`), the Mini App API returns `403`.
- No DB is used; all state is computed on-demand from `tf.yml`.

## Thunderbolt + hosts setup (script)

This repo includes a KISS setup script that uses `tf.yml` to:

- configure Thunderbolt Bridge IPv4 on each node (via SSH + `networksetup` on macOS)
- generate a managed `/etc/hosts` block and push it to all nodes

Commands:

- `make setup-env` (configures Thunderbolt bridge IPs; requires `tbnet` section per node)
- `make hosts` (writes `artifacts/hosts.block`)
- `make push-hosts` (writes `artifacts/hosts.block` and updates `/etc/hosts` on all nodes)
