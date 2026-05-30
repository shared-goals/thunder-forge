# Olla oMLX Loaded-First Balancer Proposal

Date: 2026-05-30

## Goal

Prefer an oMLX node that already has the requested model loaded in memory, then fall back to the normal least-connections choice among compatible healthy nodes.

This is for upstream Olla evaluation. Thunder Forge near-term testing should keep using the standard pinned Olla binary.

## Current Olla State

Olla already has the right routing skeleton:

1. Request inspection extracts the model and stores it in context.
2. Model registry filters to compatible endpoints that advertise the model.
3. The load balancer selects one endpoint from that eligible set.
4. The proxy retries against remaining endpoints if a connection failure occurs.

Current least-connections does not know whether a model is loaded in backend memory. It only compares active connection counts.

## Upstream Issue Context

The closest upstream issue is `thushan/olla#84`, originally about weight-based load balancing. A later comment explicitly asks for loaded-model-aware dispatch:

```text
have olla use information about loaded models to dispatch a request
- if a system has the requested model loaded, prefer it as target
- if system with model is busy and has a queue, dispatch onto idle system, if available
```

Adjacent upstream work:

- `thushan/olla#133`: sticky sessions, merged. Preserves KV/cache locality after an endpoint is selected.
- `thushan/olla#149`: open. Surfaces availability state for OpenAI-compatible backends.
- `thushan/olla#150`: open. Adds circuit-breaker/cold-start observability.

None of these implements loaded-first endpoint selection.

## Draft Implementation

Local draft checkout:

```text
.tmp/olla-upstream
```

Patch export:

```text
.tmp/olla-omlx-loaded-first-balancer.patch
```

Fork branch:

```text
sg-shag/olla feat/omlx-loaded-first-balancer
```

Local commit:

```text
3b43052 feat(balancer): add oMLX loaded-first selector
```

Published upstream PR:

```text
https://github.com/thushan/olla/pull/152
```

Published issue comment:

```text
https://github.com/thushan/olla/issues/84#issuecomment-4582723618
```

Draft patch summary:

- Adds load balancer strategy `omlx-loaded-first`.
- Reads `constants.ContextModelKey` during endpoint selection.
- Handles Olla alias routing by checking `constants.ContextModelAliasMapKey` for endpoint-specific backend model ids.
- Probes `GET /v1/models/status` on candidate endpoints.
- Caches per-endpoint loaded model state for 2 seconds.
- Uses a 300 ms status probe timeout.
- Selects by least-connections among loaded endpoints when any are found.
- Falls back to least-connections among all compatible routable endpoints when no candidate reports the model loaded or when probing fails.
- Adds unit tests for loaded preference, least-connections fallback, and alias-specific backend model lookup.

Draft config shape:

```yaml
proxy:
  load_balancer: omlx-loaded-first
```

The patch intentionally does not change model routing semantics. It only reorders endpoints after the registry has already produced the compatible set.

## Live oMLX Status Shape

Observed on `msm3-wifi.lan:8018`:

```json
{
  "model_count": 5,
  "loaded_count": 1,
  "current_model_memory": 47086500825,
  "max_model_memory": 115964116992,
  "models": [
    {
      "id": "Qwen3-Coder-Next-4bit",
      "loaded": true,
      "is_loading": false,
      "actual_size": 45082967720,
      "last_access": 1780132597.537666
    },
    {
      "id": "Qwen3.5-122B-A10B-4bit",
      "loaded": false,
      "is_loading": false,
      "actual_size": null,
      "last_access": null
    }
  ]
}
```

The draft only treats `loaded: true` as hot. It does not yet prefer `is_loading: true`.

## Expected Routing Behavior

Given compatible endpoints `msm1`, `msm2`, `msm3`, `msm4` for model `memory`:

1. If `memory` is loaded on `msm3` and `msm4`, choose the least-connected of those two.
2. If no node has `memory` loaded, choose the least-connected compatible node and let oMLX cold-load under its own LRU policy.
3. If the hot node fails connection, Olla retry removes it and selector runs again on the remaining endpoints.
4. If the request uses a public alias such as `coder-better`, probe each endpoint for the backend runtime model id resolved by Olla alias mapping.

## Maintainer / Acceptance Assessment

The Olla repo appears maintained:

- Not archived or disabled.
- Last push observed: 2026-05-23.
- Repo updated observed: 2026-05-30.
- Recent owner commits merged `#151` on 2026-05-23.
- Open PRs `#149` and `#150` have recent review activity.
- `#84` is labeled `enhancement` and `roadmap-feature`; the owner previously engaged in its discussion.

Likelihood:

- Comment/engagement on a well-scoped proposal: high.
- Acceptance of the current hardcoded oMLX-only patch as-is: medium-low.
- Acceptance of a more generic loaded-state balancer with configurable probes: medium.
- Best path: comment on `#84` first with the oMLX status contract and policy, then open a small draft PR if the maintainer likes the shape.

Why the hardcoded draft may need revision upstream:

- Probe path, timeout, and cache TTL are constants.
- It probes synchronously on a cache miss.
- It is oMLX-specific while Olla already models loaded/available states for several backend profiles.
- Upstream may prefer adding loaded-state to discovery/registry, then using a generic `loaded-first` balancer.

## Suggested Issue Comment

```markdown
I have a local oMLX use case that matches the loaded-model part of this issue.

oMLX exposes `GET /v1/models/status` with per-model `loaded`, `is_loading`, `actual_size`, and `last_access` fields. Desired policy:

1. Filter endpoints using Olla's existing model routing strategy.
2. If any compatible healthy endpoint already has the requested backend model loaded, choose least-connections among those hot endpoints.
3. If none are hot, fall back to least-connections among all compatible endpoints and let the backend cold-load.
4. For Olla model aliases, probe the endpoint-specific backend model id after alias resolution.

I have a small proof-of-concept selector named `omlx-loaded-first` that does this by probing `/v1/models/status` with a short cache. Would you prefer this as an oMLX-specific balancer, or as a generic `loaded-first` balancer backed by provider-specific status collectors?
```

## Suggested PR Shape

Title:

```text
feat(balancer): add oMLX loaded-first endpoint selection
```

PR body:

```markdown
## Summary

- Add `proxy.load_balancer: omlx-loaded-first`.
- Prefer compatible endpoints where `/v1/models/status` reports the requested model as `loaded`.
- Fall back to least-connections when no compatible endpoint is hot or probing fails.
- Preserve alias routing by probing endpoint-specific backend model ids.

## Why

Multi-model oMLX nodes advertise every local model but load lazily and evict via LRU. Least-connections alone can send a request to a cold node while another compatible node already has the model resident in memory. This improves first-token latency and reduces unnecessary model churn without changing Olla's model compatibility filtering.

## Test Plan

- `go test ./internal/adapter/balancer -run OMLXLoadedFirst`
- `go test ./internal/adapter/balancer -run Factory`
- Manual: run two oMLX endpoints, warm one model on one node, send repeated OpenAI-compatible requests through Olla, verify `X-Olla-Endpoint` prefers the hot endpoint until connection load makes fallback appropriate.
```

## Thunder Forge Near-Term Plan

Keep standard Olla binary for current cluster testing.

Recommended local behavior for now:

- Use generated Olla config with `least-connections`.
- Use sticky sessions when a client can provide a stable session id.
- Let oMLX perform lazy load and LRU eviction.
- Avoid relying on loaded-first semantics until upstream Olla accepts a feature or Thunder Forge deliberately carries a fork.

Do not switch production or dev daemons to a custom Olla build just for this experiment.