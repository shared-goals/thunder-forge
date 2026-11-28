# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Changed

- Log retention is operator-run only: `edge serve` and `usage collect-node-metrics` no longer trim, and the
  collector lost `--retention-days`. `usage trim-logs` splices live appends instead of skipping a growing log,
  keeps owner and mode, follows symlinks, and exits `1` when a file had to be skipped.
- `usage collect-node-metrics` keeps a loaded model that holds memory in `loaded_models` under its raw
  runtime id when no alias is configured, and names it in `errors`; a zero-memory adapter is dropped
  silently. A node snapshot is no longer able to hide a hand-loaded model's footprint.

### Added

- Agent rules for the live cluster, the publication boundary and secrets, plus an executable release runbook
  in `docs/operations/release.md`.

## v0.2.0 - 2026-06-02

### Added

- Unified Thunder Forge v2 runtime stack across TF edge, Olla routing, and oMLX node runtime workflows.
- Cluster-level prepare/restart/smoke/status/sync command paths for gateway, cache, and inference roles.
- Native oMLX artifact preparation and sync workflows, including fabric-aware transport behavior.
- Usage reporting pipeline from JSONL logs, including daily summaries and DuckDB query support.
- Log retention tooling for shared operational logs.
- OpenCode and Hermes client-config generation based on assigned model aliases.

### Changed

- Role model standardized to gateway, cache, and inference operation paths.
- Operational defaults and model routing behavior are now centered on tfconfig-driven v2 config.
- Bootstrap/restart flows are hardened around narrow sudo/su escalation rules for operator-managed automation.

### Fixed

- Multiple reliability fixes for daemon bootstrap/restart behavior, remote cache artifact workflows, and edge client key prompting.

### Upgrade Notes

- Treat this as the second-generation milestone release.
- Review tfconfig.yaml roles, service defaults, and model placement before rollout.
- Run config lint, regenerate Olla config, then perform bootstrap/restart before smoke checks on migrated nodes.
