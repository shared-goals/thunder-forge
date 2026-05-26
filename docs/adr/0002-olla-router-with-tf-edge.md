# ADR 0002: Olla Router with a Minimal Thunder Forge Edge

## Status

Accepted for MVP design.

## Context

ADR 0001 chose oMLX as the node-level runtime for Thunder Forge v2. The next question is the frontend/router shape once direct oMLX health is proven.

The current Thunder Forge production stack uses LiteLLM and remains valuable as a library of proven techniques. For the new architecture, the frontend should be generated from Thunder Forge desired state and should not become the source of truth.

Olla `v0.0.27` was smoked on `studio` against the development oMLX endpoint on `msm3-wifi.lan:8018`. The smoke result is recorded in `docs/notes/2026-05-26-frontend-balancer-alternatives.md`.

What Olla proved well:

- OpenAI-compatible `/v1/models` and `/v1/chat/completions` worked through the Olla provider path.
- Model alias rewriting worked.
- Dead endpoints were excluded from routing.
- Sticky sessions worked and are suitable for preserving oMLX KV/cache locality.
- Response headers expose useful routing facts such as `X-Olla-Endpoint`, `X-Olla-Routing-Strategy`, and `X-Olla-Sticky-Session`.

What Olla intentionally does not own in the smoked release:

- inbound client API-key authentication;
- per-client quota/accounting;
- root OpenAI `/v1/*` path exposure;
- Prometheus `/metrics` endpoint.

Thunder Forge also has a homelab reality:

- Caddy already exists in the homelab and is the natural external ingress for TLS, hostnames, and coarse perimeter policy.
- Internal trusted homelab services can call Thunder Forge directly.
- External access should go through Caddy.
- It is acceptable for both internal and external traffic to route through Caddy if uniform ingress/logging is useful, but it should not be mandatory for the first local MVP.

## Decision

Thunder Forge v2 will use **Olla as the router/balancer** and place a deliberately small **Thunder Forge edge** in front of it for OpenAI-compatible ingress concerns that Olla does not implement.

The MVP traffic shape is:

```text
internal trusted clients
  -> TF edge on studio
  -> Olla on studio
  -> oMLX on msm3

external clients
  -> Caddy homelab ingress
  -> TF edge on studio
  -> Olla on studio
  -> oMLX on msm3
```

An optional uniform path is allowed:

```text
all clients, including internal
  -> Caddy
  -> TF edge
  -> Olla
  -> oMLX
```

But for the first development loop, internal clients may call TF edge directly to reduce moving parts.

Responsibilities:

| Layer | Owns | Does not own |
|---|---|---|
| Thunder Forge control plane | desired state, model selection, cache/sync, node readiness, Olla config generation, smoke tests, audit contract | request proxy implementation details beyond the minimal edge |
| TF edge | root `/v1/*` compatibility, static client API-key validation, client identity tagging, stable session id injection, structured access log/accounting envelope | model routing, load balancing, node health, complex quota products |
| Olla | routing, alias rewrite, balancing, health/circuit decisions, sticky sessions, per-response endpoint attribution | inbound client auth/accounting, external TLS |
| Caddy | external TLS/hostnames, reverse proxy, optional perimeter allow/deny, optional routing for all clients | model/node semantics, TF desired state, usage accounting |
| oMLX | node-local inference runtime and model loading | cluster-level scheduling, client auth |

## Edge implementation options

### Option A: Caddy-only edge

Use Caddy directly in front of Olla. Caddy can handle TLS, host routing, path rewriting from `/v1/*` to Olla's provider path, and simple static bearer-token checks using request matchers.

Pros:

- already present in the homelab;
- no new service code;
- excellent TLS/reverse-proxy ergonomics;
- good default for external exposure.

Cons:

- per-client usage accounting is awkward;
- generating missing stable session ids is awkward;
- request/response enrichment based on Olla headers is limited;
- complex Caddyfile snippets can become a hidden application layer.

Decision: keep as a viable minimal external ingress, but do not make Caddy the TF application edge unless the MVP truly only needs path rewrite plus static auth.

### Option B: Tiny custom TF edge behind Caddy

Add a small Thunder Forge-owned proxy service on `studio` between clients/Caddy and Olla.

Minimum behavior:

- accept root OpenAI-compatible `/v1/*` paths;
- rewrite to Olla provider path, initially `/olla/openai-compatible/v1/*`;
- require a static API key for non-local or configured clients;
- map API keys to stable client ids without logging secrets;
- add `X-TF-Client-ID` to upstream requests;
- ensure a stable Olla session key exists, using caller-provided `X-Olla-Session-ID` when present and generating a deterministic/request-scoped value otherwise;
- record one structured access log line per request with client id, model, Olla endpoint header, status, latency, and request id.

Pros:

- owns exactly the missing TF semantics;
- keeps Caddy as ingress rather than application logic;
- gives a clean future path for accounting summaries;
- can be smoked locally without changing homelab Caddy.

Cons:

- introduces one small service to maintain;
- streaming proxy behavior must be tested, not hand-waved;
- static API-key config must avoid committing secrets.

Decision: prefer this for the MVP if the first Olla config generator smoke remains green. Keep it small enough that replacing it with Caddy-only remains easy.

### Option C: Full gateway platform

Use LiteLLM, Portkey, Plexus, APISIX, or Envoy AI Gateway as the main edge/router.

Pros:

- more built-in auth/quota/dashboard features;
- broader provider ecosystem.

Cons:

- heavier than the current problem;
- likely becomes another source of truth;
- duplicates Thunder Forge control-plane intent.

Decision: defer. LiteLLM remains the proven fallback/baseline, not the preferred fresh MVP architecture.

## Consequences

Positive:

- Olla handles the hard router/balancer parts it already does well.
- TF owns auth/accounting semantics without forcing Olla to grow those features.
- Caddy stays in the role it is already good at: external ingress.
- Internal development can proceed with fewer moving parts by calling TF edge directly.
- Sticky sessions are explicit and can preserve oMLX runtime cache locality.

Trade-offs:

- There are now two small frontend components in the full external path: Caddy and TF edge.
- TF edge must proxy streaming correctly before it can be used for real chat clients.
- Caddy-only remains tempting, but if accounting/session generation matters, it will become more complex than a tiny purpose-built edge.

## MVP guardrails

- Do not modify production `rock`.
- Do not disturb `msm4` / Hindsight.
- Generate Olla config from Thunder Forge desired state; do not hand-edit generated config as source of truth.
- Do not commit API keys. Use env vars or local ignored files.
- Keep tracing off by default.
- Prefer structured JSON logs for accounting before adding a database.
- Do not add PostgreSQL, a web UI, dynamic key management, or quota products until file/log-backed accounting proves insufficient.
- Smoke direct oMLX first, then Olla, then TF edge, then Caddy routing.

## Acceptance criteria

The ADR is implemented for MVP when:

1. Thunder Forge can generate an Olla config for `msm3` from desired state.
2. Olla smoke verifies health, models, chat, alias rewrite, sticky sessions, failover exclusion, and endpoint attribution headers.
3. TF edge smoke verifies root `/v1/*` compatibility, static API-key enforcement, session id behavior, and structured request log output.
4. Optional Caddy smoke verifies external-style routing to TF edge without moving auth/accounting out of TF.
5. All steps run on `studio`/`msm3` only and leave `rock` and `msm4` untouched.
