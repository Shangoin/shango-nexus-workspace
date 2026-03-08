# CLAUDE.md — Shango Nexus Workspace

> This file is read automatically by Claude Code at the start of every session.
> It is the single source of truth for project rules, architecture, and conventions.
> **Update it when the architecture changes — never rely on session memory alone.**

---

## Project Identity

- **Name:** Shango Nexus
- **What it is:** A unified AI platform fusing 13 independently-built AI pods ("Prometheus Organs") into one self-evolving system
- **Founder:** Ishaan Ghosh — solo builder, Kolkata, India
- **Contact:** team@shango.in
- **Stack:** FastAPI · Supabase (pgvector) · Redis · DEAP · Vapi · ElevenLabs · n8n · Streamlit · Next.js 15 · Docker · Render (SG + US) · Vercel (edge)
- **Current sprint:** 11 (March 2026)
- **Test count:** 136/136 (target, Sprint 11 complete)
- **GitHub:** https://github.com/Shangoin/shango-nexus-workspace

---

## Critical Architecture Rules — Never Break These

### 1. Always use the shared cascade — never call LLMs directly

```python
# CORRECT
from core.ai_cascade import cascade_call
result = await cascade_call(prompt, task_type="aurora_score", pod_name="aurora")

# WRONG — never do this in any pod
import google.generativeai as genai
model = genai.GenerativeModel("gemini-3-pro")
```

The cascade handles: provider fallback (6 LLMs), PII scrub, humanizer, Redis cache, AgentOps tracing, ID-RAG identity injection.

### 2. Always use the MCP adapter for tool calls

```python
# CORRECT — Sprint 11+ standard
from core.mcp_adapter import mcp_call
rows = await mcp_call("supabase_query", table="aurora_leads", filters={"tier": "A"})
await mcp_call("supabase_insert", table="nexus_events", data={...})

# ACCEPTABLE (legacy pods pre-Sprint 11)
from dependencies import get_supabase
sb = get_supabase()
```

New pods MUST use `mcp_call`. Existing pods can be migrated gradually.

### 3. Always use the 3-tier memory layer

```python
# CORRECT
from core.memory import remember, recall
await remember(pod="aurora", key="objection_script", value=text, redis_client=redis)
result = await recall(pod="aurora", query="objection handling script for SaaS founders")

# WRONG
await redis.set("aurora:script", text)  # never write directly to redis for business data
```

### 4. Always publish events through the bus

```python
# CORRECT
from events.bus import NexusEvent, publish
await publish(NexusEvent(pod="aurora", event_type="aurora.lead_scored", payload={...}), supabase_client=sb)

# WRONG — bypasses evolution threshold counting and cross-pod routing
```

### 5. Always validate through the constitution before any AI call with user input

```python
from core.constitution import get_constitution
constitution = get_constitution()
violation = await constitution.validate(user_text, pod="aurora")
if violation:
    return {"error": "Input rejected by constitution"}
```

### 6. Every new pod MUST register a fitness function

```python
from core.evolution import register_pod

async def _my_pod_fitness(individual: list) -> float:
    # Must return float 0.0–1.0
    ...

register_pod("my_pod", _my_pod_fitness)  # call at module import time
```

### 7. Pod fitness functions must use MAE adversarial fitness

Use `mae_adversarial_fitness()` from `core/evolution.py` as your base unless the pod has a direct numerical signal (e.g. Janus uses live P&L). Wrap it via `functools.partial(mae_adversarial_fitness, pod="my_pod")`.

### 8. Never hardcode API keys — always via config.py

```python
from config import settings
api_key = settings.gemini_api_key  # CORRECT

api_key = os.getenv("GEMINI_API_KEY")  # acceptable fallback
api_key = "sk-..."  # NEVER
```

---

## Architecture Map

```
nexus-backend/
├── main.py                    ← FastAPI app + APScheduler + all routers mounted
├── config.py                  ← pydantic-settings — single source for all 40+ env vars
├── dependencies.py            ← get_supabase(), get_redis(), verify_admin()
├── constitution.yaml          ← 6 rules, 4 circuit breakers — edit here, not in code
│
├── core/
│   ├── ai_cascade.py          ← CASCADE_CALL — entry point for all LLM calls
│   ├── mcp_adapter.py         ← MCP tool registry — Sprint 11+ standard tool interface
│   ├── memory.py              ← remember() / recall() — 3-tier memory
│   ├── evolution.py           ← genetic_cycle(), register_pod(), MAE adversarial fitness
│   ├── constitution.py        ← validate(), check_breaker(), alert_violation()
│   ├── mcts_graph.py          ← mcts_plan(), pacv_loop(), arc_select_workflow()
│   ├── genome_decoder.py      ← decode_genome(), apply_genome_to_pod(), GENE_MAP
│   ├── improvement_proofs.py  ← sign_proof_rsa(), verify_proof_rsa()
│   ├── interpretability.py    ← detect_pii_in_text(), verify_document_safety()
│   ├── encompass.py           ← encompass_branch() — parallel N-branch LLM execution
│   ├── mem1_state.py          ← mem1_step() — constant-memory multi-turn
│   ├── causal_graph.py        ← ama_causal_recall() — causal memory chains
│   └── agent_scaling_monitor.py ← 5 DeepMind scaling metrics, 30-min APScheduler
│
├── events/
│   └── bus.py                 ← publish(), subscribe(), propagate_cross_pod()
│                                 5 hardcoded cross-pod routes (see CROSS_POD_MAP)
│
├── api/
│   ├── health.py              ← GET /health — 14 subsystems + scaling metrics
│   ├── nexus.py               ← GET /api/nexus/{kpis,pods,variant-stats,scaling-health}
│   ├── evolution.py           ← POST /api/evolution/trigger/{pod_name}
│   ├── payments.py            ← Stripe + Razorpay, 6 products ($19–$299)
│   ├── realtime.py            ← GET /api/realtime/events (SSE)
│   └── razorpay_webhook.py    ← payment.captured → nexus_subscriptions + retry queue
│
└── pods/
    └── {pod_name}/
        ├── __init__.py        ← may be empty (scaffold) or register_pod() call
        └── router.py          ← FastAPI router, POST /run or domain endpoints
```

---

## Pod Registry (13 pods)

| Pod | Router path | Completion | Revenue |
|-----|-------------|------------|---------|
| `aurora` | `/api/aurora` | 70% | $99/mo Aurora Pro |
| `janus` | `/api/janus` | 80% | $49/mo DAN Pro (combined) |
| `dan` | `/api/dan` | 75% | $49/mo DAN Pro |
| `syntropy` | `/api/syntropy` | 85% | $29/pack |
| `syntropy_war_room` | `/api/war-room` | 85% | included in pack |
| `sentinel_prime` | `/api/sentinel` | 80% | $199/mo |
| `sentinel_researcher` | `/api/researcher` | 50% | included |
| `shango_automation` | `/api/automation` | 90% | $19/mo |
| `syntropy_lite` | `/api/lite` | 70% | included |
| `syntropy_scaffold` | `/api/scaffold` | 75% | included |
| `syntropy_launch` | `/api/launch` | 95% | included |
| `viral_music` | `/api/music` | 85% | à la carte |
| `ralph` | `/api/ralph` | 95% | included |

---

## Genome — GENE_MAP (8 universal genes)

```
Gene 0: temperature          — LLM temperature / creativity
Gene 1: follow_up_cadence    — timing of follow-up actions
Gene 2: opener_style         — opening approach style
Gene 3: objection_depth      — depth of objection handling
Gene 4: closing_urgency      — urgency/assertiveness in closing
Gene 5: tone_formality       — formal vs casual tone
Gene 6: content_density      — information density per response
Gene 7: personalization_level — degree of personalisation
```

Each gene is a float in [0, 1]. Pods decode them into pod-specific parameters in `core/genome_decoder.py`.

---

## Products & Pricing

| Product | Stripe ID | USD | INR |
|---------|-----------|-----|-----|
| aurora_pro | aurora_pro | $99/mo | ₹8,500/mo |
| dan_pro | dan_pro | $49/mo | ₹4,200/mo |
| sentinel_prime | sentinel_prime | $199/mo | ₹17,000/mo |
| shango_automation | shango_automation | $19/mo | ₹1,600/mo |
| syntropy_pack | syntropy_pack | $29 one-time | ₹2,500 |
| nexus_pro | nexus_pro | $299/mo | ₹25,000/mo |

---

## Sprint Format — Always Follow This

1. **File:** `tests/test_sprint{N}.py` — every sprint gets exactly one test file
2. **Test count:** Aim for 16–24 tests per sprint; cumulative total must go up
3. **Docstring format for every function:**
   ```python
   """
   Purpose:     One sentence describing what this does.
   Inputs:      Parameter types and meanings.
   Outputs:     Return type and shape.
   Side Effects: External calls (Redis, Supabase, LLM, event bus).
   """
   ```
4. **After every sprint:** Update both `PROJECT_STATUS.md` AND `NEXUS_DESIGN_EVOLUTION.md`
5. **Before any push:** Run `scripts/run_all_tests.sh` — all tests must pass
6. **Naming:** Sprint features are tagged `S{sprint_num}-{feature_num}` (e.g. `S11-01`)

---

## Coding Style

- **Async everywhere** — all FastAPI routes and core functions are `async def`
- **Pydantic models** for all request/response shapes — never raw dicts in router signatures
- **Fail-open pattern** — catch exceptions, log warning, return sensible default; never let one pod crash others
- **Fire-and-forget** for non-blocking work: `asyncio.create_task(some_long_operation())`
- **Type hints** on all public functions
- **No `import *`** — always explicit imports
- **Pod routers** must include `prefix="/api/{pod_name}"` and `tags=["{pod_name}"]`

---

## Key Environment Variables (from `.env.example`)

```
SUPABASE_URL, SUPABASE_KEY           # Database + realtime
GEMINI_API_KEY                       # Primary LLM (Gemini 3 Pro)
GROK_API_KEY                         # Groq fallback
CEREBRAS_API_KEY                     # Free 1M tokens/day fallback
MISTRAL_API_KEY, OPENROUTER_API_KEY  # Mistral + DeepSeek via OpenRouter
OPENAI_API_KEY                       # GPT-4o-mini last-resort fallback
VAPI_API_KEY, VAPI_ASSISTANT_ID      # Voice AI for Aurora
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
REDIS_URL                            # redis://redis:6379/0
ADMIN_SECRET                         # Bearer token for /api/evolution/* endpoints
SLACK_WEBHOOK_URL                    # Constitution violation alerts
AGENTOPS_API_KEY                     # Optional — cascade tracing
ALPACA_ENABLED                       # "true" to enable Janus paper trading
MAE_ADVERSARIAL                      # "true" to enable adversarial MARS fitness
```

---

## Cross-Pod Signal Routes

Defined in `events/bus.py` `_CROSS_POD_MAP`:

```
aurora.booking_failed   → syntropy.generate_resource   (skill gap → study resource)
                        → janus.analyze_objection       (financial objection → market context)
janus.regime_change     → aurora.trigger_upsell         (bull market → premium upsell)
dan.incident_detected   → sentinel_prime.analyze_incident
syntropy.quiz_completed → ralph.update_prd
```

---

## Constitution Rules (constitution.yaml)

| Rule | Type | Effect |
|------|------|--------|
| no_pii_storage | regex | Blocks emails, phone numbers, Aadhaar before AI calls |
| no_financial_advice | phrase | Blocks "guaranteed returns", "buy this stock" |
| no_harmful_content | phrase | Blocks KYC bypass, money laundering phrases |
| no_ai_speak | phrase | Blocks "delve", "utilize", "groundbreaking" etc. |
| max_prompt_tokens | token_limit | Hard cap at 32,000 tokens per call |
| rate_limit_ai | rate_limit | Max 100 AI calls per 60 seconds |

Circuit breakers: `ai_cascade` (5 failures → 60s pause), `supabase` (3 → 30s), `evolution_cycle` (2 → 300s), `vapi` (3 → 120s).

---

## Where to Start Each Session

When you open a new Claude Code session on this project:

1. This `CLAUDE.md` is already loaded — you have full context
2. Read `PROJECT_STATUS.md` to see current state and blockers
3. Read `NEXUS_DESIGN_EVOLUTION.md` (header + latest sprint section) for design decisions
4. For any sprint work: check `tests/test_sprint{current}.py` to see what's already tested

---

## Recent Research Implementations

| Paper | Implemented In | Sprint |
|-------|----------------|--------|
| DEAP genetic algorithms | `core/evolution.py` | S2 |
| MIT SEAL adaptive difficulty | `pods/syntropy_war_room/seal.py` | S3 |
| LangGraph StateGraph | `pods/dan/graph.py` | S2 |
| DeepMind SIMA multi-pod | `events/bus.py` cross-pod map | S1 |
| DeepMind Agent Scaling (180-config) | `core/agent_scaling_monitor.py` | S10 |
| MIT EnCompass N-branch | `core/encompass.py` | S10 |
| MEM1 constant-memory multi-turn | `core/mem1_state.py` | S10 |
| MIT Self-Adapting Language Models | `core/evolution.py` generate_self_edit() | S11 |
| ARC hierarchical RL workflow selector | `core/mcts_graph.py` arc_select_workflow() | S11 |
| Model Context Protocol (MCP) | `core/mcp_adapter.py` | S11 |
| Agent0 curriculum uncertainty | `core/evolution.py` curriculum_guided_challenge() | S10 |

---

## What NOT to Do

- Never duplicate cascade logic inside a pod — always import from `core/ai_cascade.py`
- Never write raw SQL — use Supabase client or `mcp_call("supabase_query", ...)`
- Never store secrets in code — use `config.py` + environment variables
- Never skip the constitution check on user-provided text
- Never create a new Supabase table without adding it to `supabase/schema.sql`
- Never push without running `scripts/run_all_tests.sh` first
- Never let a pod crash on fitness function failure — always return `0.5` as fallback
- Never use synchronous Supabase calls in async route handlers — always wrap in `asyncio.to_thread()`
