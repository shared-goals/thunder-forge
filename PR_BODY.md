## Problem

`AGENTS.md` covered only code style, status endpoints and commit naming. Nothing told an agent that this repository drives a live cluster, that a merged change is not a deployed one, or that the edge must publish which build it runs. The concrete cost showed up on 2026-11-27: the local clone was 10 commits behind `origin/main`, three different edge builds served traffic within three days, and an Olla upgrade was followed by 35 hard `502`s that nothing surfaced.

## Change

Extends `AGENTS.md` with rules in the project-rules shape already used for the owner's other repositories:

- **What this repository is** — control plane for a live cluster, not a library.
- **Work selection and tracker** — one PR per purpose; never merge your own PR or force-push a shared branch.
- **Authority, safety and the live cluster** — read-only by default; mutating targets need an explicit instruction; four separate statements for implemented / published / deployed / observed; secrets and log data stay out of Git and PR bodies; alias removal is a contract change with a client sweep.
- **Checks** — test and lint gate, docs updated with the field they describe, tests for non-trivial logic, verification stated in the PR body.
- **Deployment provenance** — `/status` and startup must publish version + commit; an empty value must read as unknown, not plausible.
- **Versioned publication** — version bump, `CHANGELOG.md`, annotated tag and release per published change, with PATCH/MINOR/MAJOR guidance.
- **Status endpoints** — keeps anonymous `/health` and `/status`, and rules that anonymous output stops at liveness, versions and node health.

## Verification

Documentation only; no code, no behaviour, no test changes. Not verified: whether the owner wants the versioned-publication rule enforced from now on — the repository has had no release since `v0.2.0` (2026-06-02), so the first merged change under this rule needs a release decision.

## Limitations

The rules describe intent, not enforcement: nothing fails a PR that skips a release or forgets a docs update. Enforcement would need CI, which this PR does not add.
