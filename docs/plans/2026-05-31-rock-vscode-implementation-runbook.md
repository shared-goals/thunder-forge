# Rock VS Code Implementation Runbook (2026-05-31)

## Goal
Move active Thunder Forge v2 implementation and validation to rock (Armbian/Linux) using a new VS Code Remote SSH session.

## 1) Open A New VS Code Session On Rock
- Use VS Code Remote SSH.
- Connect as user `shag` to `rock.lan`.
- Open folder `/mnt/samsung/thunder-forge`.

## 2) Sync Branch And Tooling On Rock
Run on rock:

```bash
cd /mnt/samsung/thunder-forge
git fetch origin
git checkout feature/omlx-runtime-mvp
git pull --ff-only origin feature/omlx-runtime-mvp
make dev-sync
```

## 3) Copy Local Config + Secrets From Studio To Rock
Run on studio:

```bash
scp /Users/shag/Work/thunder-forge/tfconfig.yaml shag@rock.lan:/mnt/samsung/thunder-forge/tfconfig.yaml
scp /Users/shag/Work/thunder-forge/.env shag@rock.lan:/mnt/samsung/thunder-forge/.env
ssh shag@rock.lan 'chmod 600 /mnt/samsung/thunder-forge/.env'
```

Policy reminder:
- `tfconfig.yaml` is the canonical non-secret operational config.
- `.env` stores secrets only.

## 4) Validate Repo Health On Rock
Run on rock:

```bash
cd /mnt/samsung/thunder-forge
make dev-lint
make dev-test
# or one command:
make dev-check
```

## 5) Validate Gateway System Setup Plan On Rock
Run on rock:

```bash
uv run thunder-forge service setup-daemon --dry-run
```

Reason:
- there is no dedicated Make target for `service setup-daemon`; use direct CLI here.

Expected:
- Frontend gateway setup path uses systemd on Linux.
- Olla and edge service checks use `systemctl`.

## 6) Apply Gateway Setup On Rock
Run on rock:

```bash
uv run thunder-forge service setup-daemon --apply --allow-sudo-prompt
```

Notes:
- Linux setup now prefers zsh when available (`/bin/zsh` or `/usr/bin/zsh`), then falls back to bash/sh.
- You may be prompted for auth based on configured admin user and sudo policy.

## 7) Restart + Smoke After Apply
Run on rock:

```bash
make restart
make status
```

Then run a smoke target as needed, for example:

```bash
make smoke infer-03
```

## 8) Continue Implementation Loop On Rock
- Make code changes in the rock session.
- Run focused checks with `make dev-lint` and `make dev-test`, then `make dev-check`.
- Keep commits small and manager-specific (frontend system services vs runtime node changes).
