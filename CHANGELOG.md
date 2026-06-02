# Changelog

All notable changes to this project will be documented in this file.

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
