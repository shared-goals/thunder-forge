# LiteLLM layer investigation — 2026-05-26

## Question

Can Thunder Forge use LiteLLM as a popular OpenAI-compatible balancing/frontend layer without adopting the full web UI / admin surface?

## Findings

LiteLLM has two relevant layers:

1. **LiteLLM Router Python SDK**
   - `from litellm import Router`
   - accepts a `model_list` with multiple deployments under the same public `model_name`;
   - exposes `router.completion()`, `router.acompletion()`, `router.embedding()`, etc.;
   - implements routing strategies such as `simple-shuffle`, `least-busy`, `usage-based-routing`, `latency-based-routing`, retries, cooldowns, fallbacks, and timeouts.

2. **LiteLLM Proxy Server**
   - exposes an OpenAI-compatible HTTP endpoint for normal clients;
   - internally sends requests through LiteLLM Router;
   - can be configured from YAML;
   - can disable Admin UI with environment variable `DISABLE_ADMIN_UI="True"`.

The proxy architecture docs describe the request path as:

`OpenAI-compatible HTTP request -> proxy_server.py -> LiteLLM Router -> litellm.completion()/embedding() -> upstream provider`

For hosted OpenAI-compatible backends, LiteLLM supports:

- `openai/<model>` provider prefix for generic OpenAI-compatible endpoints;
- `hosted_vllm/<model>` for vLLM-compatible hosted endpoints.

For oMLX, the safest first assumption is generic OpenAI-compatible routing via `openai/<omlx-model-id>` or possibly `hosted_vllm/<omlx-model-id>` only after endpoint compatibility is proven. `api_base` should include `/v1` for proxy/server configs when required by the OpenAI-compatible provider path.

## Interpretation for Thunder Forge v2

LiteLLM should remain a **thin compatibility and balancing layer**, not the source of truth.

Thunder Forge should own:

- desired state;
- node inventory;
- artifact download/sync;
- runtime start/status/smoke;
- compatibility records;
- generated LiteLLM config.

LiteLLM should provide:

- one OpenAI-compatible endpoint for clients;
- model-group aliases;
- load balancing across multiple oMLX node endpoints;
- retries/cooldowns/fallbacks;
- optional simple request metrics if useful.

Avoid depending on:

- LiteLLM Admin UI;
- dynamic model management via UI;
- LiteLLM database as Thunder Forge's source of truth;
- spend/team/key-management features for the MVP.

If using LiteLLM Proxy, disable UI:

```bash
DISABLE_ADMIN_UI=True
```

A minimal generated config shape should be enough:

```yaml
model_list:
  - model_name: qwen3-1.7b-omlx
    litellm_params:
      model: openai/Qwen3-1.7B-4bit
      api_base: http://msm3-wifi.lan:8018/v1
      api_key: dummy

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 1
  timeout: 60
```

When more nodes are added, add more entries with the same `model_name` and different `api_base` values.

## Recommendation

Use LiteLLM Proxy as the **baseline** public cluster endpoint candidate, but keep it generated and minimal:

- no Admin UI;
- no DB-backed model management for MVP;
- no production LiteLLM route until direct oMLX smoke passes;
- generated config from TF desired state;
- tests assert that generated routes use OpenAI-compatible `/v1` endpoints and public model aliases.

This is not the final frontend decision. Sergey explicitly asked for a lighter balancer/frontend alternative check: no web UI dependency, only routing/balancing, standards-compatible, and reasonably popular. The initial shortlist is recorded in `docs/notes/2026-05-26-frontend-balancer-alternatives.md`. The preferred next spike is `openziti/llm-gateway` because it is a Go single-binary OpenAI-compatible gateway with YAML config, local/OpenAI-compatible backends, health checks, and load balancing. LiteLLM remains the proven baseline until that spike is measured.

For embedded Python-only use of LiteLLM Router, it is viable but not sufficient as the cluster frontend by itself because clients still need a stable OpenAI-compatible HTTP endpoint. We could wrap Router in our own FastAPI service, but that duplicates what LiteLLM Proxy already does. KISS says: either run LiteLLM Proxy as a thin generated service with UI disabled, or adopt an even thinner gateway after a measured spike.

## Sources checked

- https://docs.litellm.ai/docs/routing
- https://docs.litellm.ai/docs/proxy/load_balancing
- https://docs.litellm.ai/docs/proxy/architecture
- https://docs.litellm.ai/docs/providers/openai_compatible
- https://docs.litellm.ai/docs/providers/vllm
- https://docs.litellm.ai/docs/proxy/ui
- https://docs.litellm.ai/docs/proxy/config_settings
