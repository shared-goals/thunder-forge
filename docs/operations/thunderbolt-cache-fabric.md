# Thunderbolt Cache Fabric Runbook

This runbook documents the split topology where one cache host is directly wired to inference nodes over Thunderbolt, while the gateway role remains separate.

## Scope

- Gateway host: runs TF edge + Olla control plane.
- Cache host: downloads/prepares model artifacts.
- Inference hosts: run oMLX daemons and receive synced artifacts.

Example physical layout:

- Cache port 1 <-> infer-01
- Cache port 2 <-> infer-02
- Cache port 3 <-> infer-03
- Cache port 4 <-> infer-04

## Design Rules

- Keep management hostnames in `nodes.<name>.host`.
- Use `nodes.<name>.fabric_host: true` to opt a node into dynamic fabric probing.
- Keep sync transport as `auto` unless testing explicit behavior.
- Do not hardcode fabric IPs in config.

## One-Time Setup

1. Cable cache host Thunderbolt ports to inference hosts one-to-one.
2. On each host, create/enable Thunderbolt network interfaces in macOS settings.
3. Verify each link has link-local or private IPv4 addressing.
4. Verify management SSH works first for every node.

Verification commands:

```bash
networksetup -listallhardwareports
ifconfig | grep -A2 -E 'bridge|thunderbolt|169\.254'
ssh infer-01 true && echo ok
```

## Thunder Forge Bootstrap

Run from the control host (gateway or operator workstation):

```bash
make bootstrap
```

Cache role bootstrap guarantees:

- oMLX tooling is installed for the cache operator user.
- Cache hub directory exists (`TF_CACHE_OMLX_MODELS_DIR` or `~/.omlx/models`).

## Sync and Download Behavior

For split topology, run artifact workflows so cache execution occurs on the cache role.

- `cluster sync` with `transport=auto`: tries Thunderbolt discovery first, falls back to management LAN.
- `cluster sync --transport fabric`: requires reachable fabric and fails if unresolved.
- `cluster sync --management`: forces management LAN.

Fabric discovery is Darwin-only and resolves at runtime from the machine executing the sync command.

## Failure Modes

- `dynamic probe unresolved`: expected fallback path in `auto` mode.
- `no reachable fabric address discovered`: expected hard failure in `fabric` mode.
- Missing cache oMLX binary: rerun `make bootstrap` or `cluster prepare --apply`.
