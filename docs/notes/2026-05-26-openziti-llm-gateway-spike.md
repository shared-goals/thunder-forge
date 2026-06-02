# openziti/llm-gateway spike — 2026-05-26

## Question

Can `openziti/llm-gateway` act as a smaller TF v2 frontend/balancer than LiteLLM for an oMLX node runtime?

The spike is intentionally narrow:

- run gateway on `studio` only;
- proxy one existing oMLX backend on `msm3`;
- do not touch production `rock`;
- do not touch `msm4` / Hindsight;
- prove OpenAI-compatible `/health`, `/v1/models`, and one non-streaming `/v1/chat/completions` request;
- verify whether API keys and Prometheus metrics are sufficient for MVP operator visibility.

## Environment

- Host: `studio` / macOS arm64
- Repo: `/Users/shag/Work/thunder-forge`
- Gateway candidate: `openziti/llm-gateway` `v0.1.4`
- Binary asset: `llm-gateway_0.1.4_darwin_arm64.tar.gz`
- Backend: `http://msm3-wifi.lan:8018`
- Backend runtime: existing `omlx serve --host 0.0.0.0 --port 8018` under `shag`
- Backend model visible through oMLX: `Qwen3-1.7B-4bit`

`go` was not installed on `studio`, but this was not a blocker because upstream ships a macOS arm64 binary.

## Install / binary check

Downloaded release metadata from GitHub API and selected the macOS arm64 asset.

The release checksum file uses `./`-prefixed names, so checksum verification had to normalize paths before matching.

Verified:

```text
checksum_ok True
sha256 b4b9a2e316ea1700c6f8244005faa11f5613abdfe688b785f9090b1877027ca5
member CHANGELOG.md
member LICENSE
member README.md
member llm-gateway
```

Binary help:

```text
OpenAI-compatible API proxy to OpenAI/Anthropic/local backends via zrok

Usage:
  llm-gateway [command]

Available Commands:
  completion  Generate the autocompletion script for the specified shell
  genkey      Generate a new gateway API key
  help        Help about any command
  run         Run the llm-gateway server
  version     Show the llm-gateway version
```

## Minimal proxy config tested

```yaml
listen: ":18181"

providers:
  local:
    base_url: "http://msm3-wifi.lan:8018"

metrics:
  enabled: false

tracing:
  enabled: false
```

No zrok/OpenZiti feature was required for local LAN operation.

## Minimal proxy smoke results

Started on `studio`:

```bash
.tmp/llm-gateway-spike/llm-gateway run .tmp/llm-gateway-spike/config.yaml
```

Health through gateway:

```text
GET http://127.0.0.1:18181/health
200 {"status":"ok"}
```

Models through gateway:

```text
GET http://127.0.0.1:18181/v1/models
200 {"object":"list","data":[{"id":"Qwen3-1.7B-4bit","object":"model","created":1779786276,"owned_by":"omlx"}]}
```

Chat completion through gateway:

```text
POST http://127.0.0.1:18181/v1/chat/completions
model: Qwen3-1.7B-4bit
status: 200
latency: 0.411s
```

Response excerpt:

```json
{
  "id": "chatcmpl-e880557d",
  "object": "chat.completion",
  "model": "Qwen3-1.7B-4bit",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Okay, the user just said ..."
      },
      "finish_reason": "length"
    }
  ]
}
```

This proves the gateway can proxy the oMLX OpenAI-compatible endpoint for direct model ids.

## Alias behavior

Tested a TF-style public alias:

```text
model: qwen3-1.7b-omlx-msm3-test
```

Result:

```text
HTTP 404
{"error":{"message":"model not found","type":"not_found_error"}}
```

Interpretation: with minimal local-provider config, `llm-gateway` forwards the requested model id to the backend. It does not provide LiteLLM-style `model_name` → upstream `model` aliasing in the simple config path.

This is not a blocker for the TF v2 MVP if Thunder Forge accepts a naming convention based on real backend model ids and lets clients choose from `/v1/models`.

## Missing-model behavior

Tested request with no `model` field:

```text
HTTP 400
{"error":{"message":"model is required","type":"invalid_request_error"}}
```

So semantic/default routing is not automatically available in this minimal config. That is fine for TF v2 MVP because explicit model ids are preferable, but it means we should not assume model omission works without configuring semantic routing.

## API keys + metrics config tested

Second local run used explicit virtual API keys and metrics:

```yaml
listen: ":18080"

metrics:
  enabled: true

api_keys:
  enabled: true
  keys:
    - name: shag
      key: "sk-gw-local-spike-shag"
      allowed_models: ["Qwen3-1.7B-4bit"]
    - name: restricted
      key: "sk-gw-local-spike-restricted"
      allowed_models: ["not-this-model"]

providers:
  local:
    endpoints:
      - name: msm3
        base_url: "http://msm3-wifi.lan:8018"
        weight: 1
    health_check:
      interval_seconds: 30
      timeout_seconds: 5
```

These keys are throwaway local spike values, not production credentials.

## API keys + metrics smoke results

Direct backend check before gateway:

```text
GET http://msm3-wifi.lan:8018/health
{"status":"healthy","default_model":"Qwen3-1.7B-4bit",...}

GET http://msm3-wifi.lan:8018/v1/models
Qwen3-1.7B-4bit
```

Gateway health:

```text
GET http://127.0.0.1:18080/health
{"status":"ok"}
```

Unauthenticated model listing is rejected when API keys are enabled:

```text
GET http://127.0.0.1:18080/v1/models
HTTP/1.1 401 Unauthorized
{"error":{"message":"API key required","type":"authentication_error"}}
```

Authenticated model listing works:

```text
GET http://127.0.0.1:18080/v1/models
Authorization: Bearer sk-gw-local-spike-shag

{
  "object": "list",
  "data": [
    {
      "id": "Qwen3-1.7B-4bit",
      "object": "model",
      "owned_by": "omlx"
    }
  ]
}
```

Authenticated chat works:

```text
POST http://127.0.0.1:18080/v1/chat/completions
Authorization: Bearer sk-gw-local-spike-shag
model: Qwen3-1.7B-4bit
status: 200
```

Response excerpt:

```json
{
  "id": "chatcmpl-d1eafb9d",
  "object": "chat.completion",
  "model": "Qwen3-1.7B-4bit",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Okay, the user just said \"Reply with exactly: thunder ok.\""
      },
      "finish_reason": "length"
    }
  ],
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 16,
    "total_tokens": 30
  }
}
```

A restricted key is denied for a disallowed model:

```text
POST http://127.0.0.1:18080/v1/chat/completions
Authorization: Bearer sk-gw-local-spike-restricted
model: Qwen3-1.7B-4bit

HTTP/1.1 403 Forbidden
{"error":{"message":"model 'Qwen3-1.7B-4bit' is not allowed for this API key","type":"permission_error"}}
```

Prometheus metrics are exposed at `/metrics`. Observed useful labels:

```text
llm_gateway_requests_total{key="shag",model="Qwen3-1.7B-4bit",provider="local",streaming="false"} 1
llm_gateway_request_duration_seconds_sum{key="shag",model="Qwen3-1.7B-4bit",provider="local"} 0.24091475
llm_gateway_request_duration_seconds_count{key="shag",model="Qwen3-1.7B-4bit",provider="local"} 1
llm_gateway_requests_inflight 0
llm_gateway_tokens_prompt_total{model="Qwen3-1.7B-4bit",provider="Qwen3-1.7B-4bit"} 14
llm_gateway_tokens_completion_total{model="Qwen3-1.7B-4bit",provider="Qwen3-1.7B-4bit"} 16
```

Important metric gap: no per-request `endpoint` label was observed. The docs mention `llm_gateway.endpoint.healthy{endpoint=...}`, but the local one-endpoint spike did not emit an endpoint-health sample in the captured output, and request metrics were attributed to `provider="local"`, not `endpoint="msm3"`.

Source inspection confirms the request metrics include `provider`, `model`, `streaming`, and `key`, while multi-endpoint request selection knows the endpoint internally but does not attach endpoint name to request metrics.

## Load-balancing and observability fit

`llm-gateway` declares and implements a multi-endpoint local provider with:

- weighted round-robin selection;
- health checks using `/v1/models` then `/api/tags` fallback;
- passive failover on network errors;
- deduplicated `/v1/models` union across healthy endpoints.

This is enough for MVP routing across homogeneous node runtimes. It is not a GPU-load-aware, queue-aware, or least-busy scheduler.

For API-key and usage visibility:

- per-key request count: yes;
- per-key/model request duration: yes;
- token counters by model: yes for non-streaming responses with `usage`;
- model restriction by key: yes;
- per-key `/v1/models` filtering: not implemented upstream;
- per-key rate limits / expiry / dynamic key management: not implemented upstream;
- per-node request attribution: not available in current request metrics.

## Verdict: STRONG MVP CANDIDATE WITH OBSERVABILITY GAP

### What worked

- Single macOS arm64 binary, no local Go install needed.
- Tiny YAML config.
- No DB, queue, sidecar, web UI, or zrok/OpenZiti dependency required for local LAN use.
- `/health` works.
- `/v1/models` proxies oMLX correctly.
- `/v1/chat/completions` proxies oMLX correctly with backend model id.
- Virtual API keys work for authentication and model allowlists.
- Prometheus request metrics include key/model labels.
- Operational shape is much smaller than LiteLLM Proxy.

### What did not work / open issue

- Public aliasing did not work in the minimal config. This is acceptable for MVP if TF v2 uses backend model ids plus naming conventions.
- No-model semantic/default routing did not work without extra config.
- Per-request endpoint/node attribution is missing from observed metrics.
- Endpoint health metric was documented but not observed in the one-endpoint local capture; this needs a two-endpoint or longer health-cycle check before relying on it.
- Community/popularity is much smaller than LiteLLM or Portkey, even though the project is technically aligned.

### Recommendation for TF v2

Use `openziti/llm-gateway` as the preferred lightweight TF v2 frontend candidate for the MVP, with explicit scope:

1. expose real oMLX model ids through `/v1/models`;
2. use naming conventions instead of public aliases for the first MVP;
3. use virtual API keys for trusted users/agents;
4. scrape Prometheus metrics for key/model/request/latency/token summaries;
5. treat per-node request attribution as a post-MVP gap unless a tiny upstream patch adds an `endpoint` metric label.

LiteLLM remains the proven baseline from production TF, but the current evidence favors `llm-gateway` for the new minimal frontend if we do not need LiteLLM's aliasing/admin surface.

## Next steps after this iteration

1. Record this spike in ADR/plan.
2. Add a generated `llm-gateway` dev config from TF desired state.
3. Add a tiny CLI smoke command for gateway `/health`, authenticated `/v1/models`, authenticated non-streaming chat, and `/metrics` label check.
4. Later, run a two-endpoint simulation or use another msm node to verify weighted round-robin and endpoint health behavior.
5. If node-level attribution becomes important before MVP release, either patch upstream `llm-gateway` or collect node split from oMLX/node logs instead of the gateway.
6. Only after frontend config generation is in place, proceed to durable `runtime install --node msm3` launchd support.
