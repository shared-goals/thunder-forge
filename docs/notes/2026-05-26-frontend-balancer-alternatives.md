# Frontend / balancer alternatives for TF v2 — 2026-05-26

## Question

Thunder Forge v2 needs a small frontend layer after direct oMLX runtime health is proven. The desired frontend should be:

- OpenAI-compatible for normal clients;
- able to route/load-balance across one or more oMLX node endpoints;
- lightweight;
- preferably no web UI / console dependency;
- popular or at least operationally credible;
- not the source of truth for Thunder Forge state.

LiteLLM was used first because it is proven in the current Thunder Forge stack, but it should not be treated as the final choice without checking lighter alternatives.

## Shortlist

| Candidate | Fit | Pros | Cons | Current decision |
|---|---|---|---|---|
| LiteLLM Proxy | High baseline | Known in current TF, OpenAI-compatible, model groups, load balancing, retries/cooldowns, YAML config, can disable Admin UI | Python service, broader gateway/admin surface than TF v2 currently needs, past routing behavior must be measured rather than trusted | Keep as proven baseline, not final architecture |
| openziti/llm-gateway | Highest lightweight fit | Go single binary, YAML config, OpenAI-compatible proxy, local/OpenAI-compatible backends, weighted round-robin, health checks, passive failover, no DB/queue/required web UI | Young project, small community, extra zero-trust/zrok features not needed for MVP | Preferred alternative spike |
| Portkey Gateway | High popularity | MIT, popular, fast/tiny-footprint claims, routing/retries/fallbacks/load balancing, OpenAI-compatible | TypeScript stack, console/web surface, broad guardrails/enterprise feature set; likely not “only balancer” | Keep as fallback if popularity matters more than minimalism |
| Nayjest/lm-proxy | Medium-low | Python/FastAPI, OpenAI-compatible, standalone or library, config-driven routing | Smaller project, less clearly focused on simple multi-endpoint load balancing | Reference only for now |
| Apache APISIX AI Gateway | Low for MVP | Mature gateway, load balancing/retry/rate-limit plugins | Full API gateway infra, heavier than needed | Defer |
| Envoy AI Gateway | Low for MVP | Serious CNCF/Envoy ecosystem, strong future platform option | Kubernetes/Envoy Gateway/two-tier pattern, much heavier than TF v2 MVP | Defer |

## Recommendation

A small `openziti/llm-gateway` spike was run on `studio`; details are in `docs/notes/2026-05-26-openziti-llm-gateway-spike.md`.

Result: **strong MVP validation with a clear observability gap**.

What worked:

1. macOS arm64 prebuilt binary works on `studio` without installing Go.
2. Tiny local YAML config worked without zrok/OpenZiti features.
3. `/health` proxied successfully.
4. `/v1/models` proxied oMLX successfully.
5. `/v1/chat/completions` proxied oMLX successfully for backend model id `Qwen3-1.7B-4bit`.
6. Virtual API keys worked for authentication and per-key model allowlists.
7. Prometheus metrics exposed per-key/per-model request counts and latency, plus token counters for non-streaming responses.

What did not work or remains limited:

1. TF-style public alias `qwen3-1.7b-omlx-msm3-test` failed with `model not found` because the gateway/backend path used the requested model id as-is. This is acceptable for the MVP if TF v2 uses real oMLX model ids plus naming conventions and lets users/agents discover choices from `/v1/models`.
2. Omitting `model` failed with `model is required`; semantic/default routing is not automatic in minimal config.
3. Per-request node/endpoint attribution is not present in observed request metrics. The gateway can health-check and weighted-round-robin endpoints, but request metrics are labeled by `provider`, `model`, `streaming`, and `key`, not by endpoint such as `msm3`.
4. Per-key `/v1/models` filtering, per-key rate limits, expiry, hashed key storage, and dynamic key management are not implemented upstream.

Current recommendation: make `llm-gateway` the preferred lightweight TF v2 frontend candidate for MVP, while keeping LiteLLM as the production-proven baseline. The MVP should explicitly accept backend model ids and treat per-node request attribution as post-MVP unless a small upstream patch adds an `endpoint` metric label.

## Why not switch away from LiteLLM immediately?

LiteLLM remains useful as a baseline because it is already understood by the current production Thunder Forge stack. The right decision is not “LiteLLM bad, replace now”; it is:

```text
direct oMLX green -> generated LiteLLM route baseline -> llm-gateway proxy/API-key/metrics spike -> choose lightweight MVP frontend -> generate gateway config
```

That keeps the system measurable and avoids mixing runtime bugs with frontend/router bugs.

## Acceptance criteria for choosing openziti/llm-gateway

It becomes the preferred TF v2 frontend if:

- the binary/config path is simpler than LiteLLM for one node;
- OpenAI-compatible clients work without code changes;
- oMLX `/v1/models` and `/v1/chat/completions` work through it with real backend model ids;
- virtual API keys are enough for trusted-user/agent MVP access control;
- Prometheus metrics are enough for per-key/per-model request and latency summaries;
- health/failover behavior is inspectable enough for TF operations;
- config generation from TF desired state is straightforward;
- it does not require adopting zrok/OpenZiti networking features for local LAN operation.

Known accepted MVP gaps:

- no LiteLLM-style public aliasing in the minimal local-provider path;
- no per-request endpoint/node label in observed metrics;
- no per-key `/v1/models` filtering, expiry, dynamic key API, or rate limiting.

If these gaps become immediate requirements, keep LiteLLM as the baseline or patch `llm-gateway` before adopting it.

## Sources checked

- https://docs.litellm.ai/docs/routing
- https://docs.litellm.ai/docs/proxy/load_balancing
- https://docs.litellm.ai/docs/proxy/architecture
- https://github.com/openziti/llm-gateway
- https://github.com/Portkey-AI/gateway
- https://github.com/Nayjest/lm-proxy
- https://apisix.apache.org/ai-gateway/
- https://github.com/envoyproxy/ai-gateway

## Addendum: Olla and Plexus closer look (2026-05-26)

Follow-up comparison requested after the initial `llm-gateway`/LiteLLM/Portkey/TensorZero/Bifrost scan.

### Concrete repo/codebase snapshot

Shallow clones inspected under `/tmp/tf-gateway-close-look`.

| Candidate | Stack | License | Repo size | Approx inspected source/docs lines | Shape |
|---|---|---:|---:|---:|---|
| `openziti/llm-gateway` | Go | Apache-2.0 | 540 KB | 62 files / ~9.6k lines | single binary, YAML, no DB |
| `thushan/olla` | Go | Apache-2.0 | 12 MB | 540 files / ~148k lines | single binary, richer proxy/balancer, YAML, no DB |
| `mcowger/plexus` | Bun/Fastify/TS + React + Drizzle | MIT | 35 MB | 982 inspected files / ~383k lines (JSON-heavy) | app/gateway with DB + Admin UI |

Numbers are not exact product complexity, but they are useful for KISS gravity: `llm-gateway` is tiny, Olla is real but still single-binary, Plexus is already an application platform.

### `llm-gateway` vs `thushan/olla`

Olla is the first alternative that makes me less certain about `llm-gateway` as the default frontend. It is still Go/single-binary/YAML/no-DB, but is much more focused on LLM load balancing as a product.

#### Where Olla looks stronger

- **Purpose fit:** explicit “proxy and load balancer for LLM infrastructure”, not zero-trust/networking-first.
- **Backend model:** static endpoint inventory with provider profiles; supports Ollama, LM Studio, LiteLLM, vLLM, SGLang, llama.cpp, vLLM-MLX, and generic OpenAI-compatible backends.
- **Balancing:** round-robin, least-connections, priority; default config uses least-connections. For TF this may be better than plain weighted RR once there are multiple msm nodes.
- **Sticky sessions:** explicit KV-cache-affinity mode (`X-Olla-Session-ID`, prefix hash, auth header hash, etc.). Not needed for first MVP, but very relevant once agents run long multi-turn sessions.
- **Model routing:** strict/optimistic/discovery modes plus alias rewriting. This is closer to TF’s desired “public model name → backend model id(s)” story than `llm-gateway`’s currently observed minimal local-provider behavior.
- **Capability filtering:** request inspector detects vision (`image_url`/`image`) and embeddings; routing can filter to capable models/endpoints.
- **Ops introspection:** internal endpoints like `/internal/health`, `/internal/status/endpoints`, `/internal/status/models`, `/internal/stats/models`, `/internal/stats/sticky`, `/internal/process`.
- **Rate/request limits:** built-in global/IP/endpoint rate limits and body/header limits.
- **Endpoint attribution:** Olla response headers include endpoint/routing info (documented `X-Olla-Endpoint` etc.); this likely solves the “which node served this?” observability gap more directly than current `llm-gateway` metrics.

#### Where Olla looks weaker / less MVP-simple

- **No built-in client authentication.** Its own security docs say “No authentication built-in (use reverse proxy)”. For TF trusted-MVP this is acceptable behind localhost/LAN, but `llm-gateway` virtual API keys were a nice immediate fit for Hermes/agent access separation.
- **More moving parts in config.** Profiles, model registry, discovery, alias resolver, translator registry, security/rate-limit config. Still YAML, but more concepts than `llm-gateway`.
- **URL surface is not a pure OpenAI root by default.** Olla exposes `/olla/proxy/v1/chat/completions` and provider-specific paths. That may be fine if clients can set base URL to `/olla/proxy`, but needs a smoke test with Hermes/OpenAI SDK/Open WebUI expectations.
- **Prometheus story unclear from quick code scan.** Olla has internal stats endpoints and structured logs, but I did not confirm a `/metrics` Prometheus endpoint equivalent to `llm-gateway`.
- **Audio endpoints not obvious.** It handles/buffers audio content-types and generic proxy paths, but I did not find explicit OpenAI `/v1/audio/transcriptions` first-class support like Plexus has. For current TF v2 MVP, audio is already out of scope.

#### Direct comparison for TF v2 MVP

| Requirement | `llm-gateway` | Olla |
|---|---|---|
| Single binary / no DB | yes | yes |
| YAML-generated config from TF desired state | easy | possible, more schema work |
| OpenAI-compatible chat to oMLX | proven in spike | likely, needs smoke test |
| `/v1/models` aggregation | yes | yes, plus unified model views |
| Health/failover | yes, weighted RR + passive failover | yes, richer health/circuit/balancer logic |
| Model aliases | weak/absent in minimal path | built-in alias resolver/rewrite |
| Endpoint/node attribution | gap in request metrics | likely stronger via headers/stats |
| API keys | built-in virtual keys | absent; needs reverse proxy or TF wrapper |
| Prometheus metrics | confirmed | not confirmed |
| Cognitive footprint | very small | medium |

### `mcowger/plexus` closer look

Plexus is not a lightweight balancer; it is a full universal LLM gateway / transformation layer.

Strong points:

- **Broad API coverage:** `/v1/chat/completions`, `/v1/responses`, `/v1/messages`, Gemini `/v1beta`, embeddings, `/v1/audio/transcriptions`, `/v1/audio/speech`, image generations/edits.
- **Transformation/routing:** model aliases, provider adapters, request/response transformations, OpenAI/Anthropic/Gemini/Ollama/OAuth provider support.
- **Admin + management:** Admin UI and `/v0/management/*` API for providers, aliases, keys, quotas, config export/import.
- **Auth/quotas/accounting:** API keys, quotas, cost/token/latency tracking, dashboard.
- **Vision fallthrough:** can convert images to text descriptions for non-vision target models.
- **Stateful Responses API:** stores response/conversation state in DB and cleans it up.

Reasons it is probably too much for TF v2 MVP:

- Requires persistent DB state (`DATABASE_URL`; SQLite default, Postgres supported). That makes Plexus a source-of-truth candidate, exactly what TF v2 is trying to avoid for the frontend.
- Bun/TypeScript/React/Drizzle/Admin UI stack is much heavier operationally than a Go binary.
- It overlaps with LiteLLM’s “platform gateway” gravity rather than replacing it with a smaller balancer.
- Good choice if we decide media/OAuth/quotas/Admin UI are product requirements now. Bad choice if the question is simply “front oMLX nodes with a thin reliable gateway”.

### Updated recommendation

I would change the previous recommendation from **“llm-gateway is preferred lightweight candidate”** to:

1. **Run an Olla smoke spike before committing to `llm-gateway`.** Olla is close enough to the desired TF v2 router/balancer shape that skipping it would be premature.
2. Keep **`llm-gateway` as the simplest known-green baseline**: already smoked with oMLX on `studio → msm3`, virtual API keys and Prometheus metrics work, and config generation should be trivial.
3. Treat **Olla as the strongest architectural candidate** if it passes three checks:
   - Hermes/OpenAI clients can use it cleanly through an OpenAI-compatible base URL without awkward path hacks.
   - It gives reliable endpoint/node attribution through headers/stats for every completion.
   - Lack of built-in auth is acceptable or solved cleanly with a tiny local reverse-proxy/API-key layer.
4. Treat **Plexus as a feature-rich fallback**, not MVP frontend. It belongs in the same “heavy platform gateway” bucket as LiteLLM/Portkey, with better media coverage but more state.

### Olla smoke result (2026-05-26)

Smoke ran on `studio` with Olla release `v0.0.27` (`darwin/arm64`) from `/tmp/tf-gateway-close-look/olla-bin/olla`, using a local dev config at `/tmp/tf-gateway-close-look/olla-bin/tf-olla-smoke.yaml`.

Config shape:

- listen: `127.0.0.1:40115`;
- proxy engine: `olla`;
- load balancer: `least-connections`;
- sticky sessions: enabled with `session_header`, `prefix_hash`, `auth_header`;
- endpoints:
  - healthy: `msm3-omlx-live` → `http://msm3-wifi.lan:8018`, type `openai-compatible`;
  - intentionally dead: `dead-endpoint-for-failover` → `http://127.0.0.1:59999`;
- alias: `qwen3-1.7b-omlx-msm3-test` → `Qwen3-1.7B-4bit`.

Observed results:

1. `/internal/health` returned `200 {"status":"healthy"}`.
2. `/version` returned Olla `v0.0.27`, Community edition.
3. `/internal/status/endpoints` showed `msm3-omlx-live` healthy and `dead-endpoint-for-failover` offline. This proves dead endpoints are excluded from the routable set at health/discovery level.
4. `/internal/status/models` discovered one oMLX model: `Qwen3-1.7B-4bit`.
5. `/olla/proxy/v1/models` and `/olla/openai-compatible/v1/models` both returned OpenAI-style model lists with `Qwen3-1.7B-4bit`.
6. `/olla/proxy/v1/chat/completions` successfully proxied non-streaming chat to oMLX with the real model id.
7. `/olla/openai-compatible/v1/chat/completions` also successfully proxied non-streaming chat; clients can use base URL `http://host:40115/olla/openai-compatible/v1`.
8. Alias rewrite worked: request model `qwen3-1.7b-omlx-msm3-test` routed to `msm3-omlx-live`; backend response model was `Qwen3-1.7B-4bit`.
9. Response headers are strong for TF observability:
   - `Via: 1.1 olla/v0.0.27`
   - `X-Olla-Backend-Type: openai-compatible`
   - `X-Olla-Endpoint: msm3-omlx-live`
   - `X-Olla-Model: ...`
   - `X-Olla-Routing-Decision: routed`
   - `X-Olla-Routing-Reason: model_found` or alias-resolution reason
   - `X-Olla-Routing-Strategy: strict` or `alias`
   - `X-Olla-Sticky-Session: miss/hit`
   - `X-Olla-Sticky-Key-Source: session_header/prefix_hash`
10. Sticky sessions worked: first request with `X-Olla-Session-ID` was a miss, later requests with the same session were hits; `/internal/stats/sticky` showed `hits: 2`, `misses: 2`.
11. Root `/v1/models` returned `404`; Olla does **not** expose a pure OpenAI root path by default. Clients must use `/olla/proxy/v1` or provider path `/olla/openai-compatible/v1`, or TF must front it with a tiny path-rewriting/auth layer.
12. `/metrics` returned `404`; Prometheus metrics are not present in the release smoke. Olla has internal JSON stats/status endpoints and structured logs instead.
13. `/internal/stats/models` returned empty model request stats even after successful proxy requests, while `/internal/status` and `/internal/status/endpoints` did record endpoint request count, success rate, latency, and traffic. So per-endpoint operational status works, but model-level stats in this release are either not wired for this path/profile or not useful for TF accounting.

Artifacts:

- `/tmp/tf-gateway-close-look/olla-bin/tf-olla-smoke.yaml`
- `/tmp/tf-gateway-close-look/olla-bin/smoke-results.txt`
- `/tmp/tf-gateway-close-look/olla-bin/smoke-results-followup.txt`
- `/tmp/tf-gateway-close-look/olla-bin/smoke-results-extra.txt`

### API key / auth / quota research (2026-05-26)

Important distinction for TF:

- **Inbound client authorization/accounting**: clients call TF/Olla with an API key so TF can allow/deny and attribute usage.
- **Outbound endpoint authentication**: Olla calls protected upstream backends with their API keys/tokens.

Olla `v0.0.27` does **not** implement inbound client API-key enforcement. Its docs still say “No authentication built-in (use reverse proxy)”. OpenAI-compatible frontend docs also say Olla does not issue/validate API keys and recommend Nginx/Traefik/Caddy/API-gateway style enforcement for external exposure; for per-key quotas/logs they explicitly point to a real API gateway.

GitHub search findings:

- `thushan/olla#132` — **open**, “auth support for ollama cloud models without proxy”. This is about outbound endpoint auth. Maintainer agrees it is needed. The design evolved into per-endpoint auth: bearer, API key, basic, env/file secrets, custom headers. Not inbound client auth/accounting.
- `thushan/olla#146` — **open PR**, “feat: Authentication for local backends”. Implements the outbound endpoint-auth design for local backends: bearer/api_key/basic, health/model-discovery/proxy auth injection, secret redaction, Retry-After handling, etc. Still not inbound client API-key validation.
- `thushan/olla#47` — **open**, “Explicitly define models”. Mentions provider `api_key` in a Cloudflare Workers AI/provider-definition context; maintainer notes auth is prerequisite. Again outbound/provider auth, not client quota accounting.
- `thushan/olla#104` — **closed**, “How to assume endpoint is healthy”; user asked about different endpoint API keys. Maintainer suggested LiteLLM for remote APIs and noted Olla is local-backend oriented.
- Searches for `quota`, `accounting`, and inbound client auth did not reveal an Olla-native per-client quota/accounting roadmap issue.

Conclusion: for TF MVP, Olla should be treated as a strong router/balancer, **not** as the API-key authority or usage-accounting system.

### Updated decision after Olla smoke

Olla passes the important router/balancer checks better than `llm-gateway`:

- OpenAI-compatible chat/models to oMLX: **green**.
- Alias rewrite: **green**.
- Dead endpoint excluded / healthy endpoint used: **green**.
- Per-response node attribution: **green and better than llm-gateway**.
- Sticky session support: **green**.

Olla does **not** replace the need for a TF-facing auth/accounting layer:

- no inbound client API-key validation;
- no per-client quotas/accounting;
- no root `/v1` path without prefix;
- no Prometheus `/metrics` in the smoked release;
- model stats endpoint did not reflect proxied requests in this smoke.

Recommendation for TF v2 MVP:

1. Use **Olla as the preferred frontend/router candidate** for the next MVP design, with `llm-gateway` demoted to the simpler fallback.
2. Add a tiny TF-owned edge layer in front of Olla for the things Olla intentionally does not own:
   - accept root OpenAI-compatible `/v1/*` paths;
   - validate static API keys for trusted clients;
   - attach client identity for logs/usage;
   - optionally enforce coarse per-key limits later.
3. Keep that edge layer deliberately small. It should not become a second LiteLLM. For KISS/DRY/YAGNI, initial edge can be Caddy/Nginx if static key validation is enough, or a tiny Go service if TF needs request accounting from day one.
4. Do not wait for Olla outbound-auth PR #146 for the local oMLX MVP; oMLX does not currently require upstream auth. Track #146 only for future protected nodes/cloud backends.
