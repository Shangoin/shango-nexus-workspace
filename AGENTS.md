# Shango Nexus — Agent Instructions

> **This file is the authoritative instruction set for all AI coding agents.**
> Read every word before writing a single line of code.

---

## CONTEXT: WHAT IS ALREADY BUILT (DO NOT REBUILD)

The following are LIVE in `nexus-backend/` — **import them, never rewrite them**:

| File | Purpose |
|------|---------|
| `core/ai_cascade.py` | 6-LLM cascade, Redis+LRU cache, PII scrub, humanizer |
| `core/memory.py` | 3-tier memory (Redis→pgvector→mem0) |
| `core/evolution.py` | DEAP genetic engine, 50-pop, 10-gen, `register_pod()` |
| `core/constitution.py` | YAML rules, circuit breakers, `validate()`, `check_breaker()` |
| `core/mcts_graph.py` | MCTS UCB1 planner, `pacv_loop()` |
| `events/bus.py` | pub/sub event bus, 5 cross-pod signal routes |
| `api/payments.py` | Stripe + Razorpay unified catalogue |
| `supabase/schema.sql` | 11 tables including nexus_memories (pgvector) |
| `docker-compose.yml` | All 4 services live |
| `render.yaml` | Production deploy config |

Aurora 1.0 (`pods/aurora/`) has MARS loop, 9-category critique, Vapi ARIA, geo-routing,
nurture engine — all working. **DO NOT touch these unless explicitly instructed.**

---

## SPRINT INVENTORY (EXECUTION ORDER IS MANDATORY)

### Sprint 2 — Wire Everything (Revenue Now)
| Task | File | Status |
|------|------|--------|
| S2-01 | `core/genome_decoder.py` | ✅ Built |
| S2-02 | `pods/dan/graph.py` + update `pods/dan/router.py` | ✅ Built |
| S2-03 | `pods/aurora/reconstructive_memory.py` | ✅ Built |
| S2-04 | `pods/aurora/brain.py` | ✅ Built |
| S2-05 | Updated `api/payments.py` (Razorpay) | ✅ Built |
| S2-06 | Updated `core/constitution.py` (Slack alerts) | ✅ Built |
| S2-07 | Updated `nexus-dashboard/dashboard.py` (live events) | ✅ Built |
| S2-08 | `n8n/aurora_to_syntropy_cross_pod.json` | ✅ Built |

### Sprint 3 — Prometheus Intelligence Layer
| Task | File | Status |
|------|------|--------|
| S3-01 | `pods/aurora/rl_variants.py` | ✅ Built |
| S3-02 | `pods/syntropy_war_room/seal.py` | ✅ Built |
| S3-03 | `pods/janus/market_feed.py` | ✅ Built |
| S3-04 | Updated `core/ai_cascade.py` (AgentOps tracing) | ✅ Built |

### Sprint 4 — Alien Intelligence
| Task | File | Status |
|------|------|--------|
| S4-01 | `core/interpretability.py` | ✅ Built |
| S4-02 | `core/improvement_proofs.py` | ✅ Built |
| S4-03 | `pods/aurora/proactive_scout.py` | ✅ Built |

---

## CODING CONTRACTS (ALL FILES MUST FOLLOW)

Every file you create must:
1. Import shared core (`from core.ai_cascade import cascade_call` etc.)
2. Register with the event bus (`from events.bus import publish`)
3. Respect the constitution (`from core.constitution import validate, check_breaker`)
4. Write async Python (FastAPI standard throughout)
5. Include a docstring with: **Purpose, Inputs, Outputs, Side Effects**
6. Call `check_breaker("correct_breaker_name")` before ANY external API
7. Publish an event on completion (`await publish(...)`)
8. **Never raise unhandled exceptions** — return `None`/`False` on error
9. Log to `nexus_events` table on every meaningful action

---

## GENOME MAP (universal across all pods)

```python
GENE_MAP = {
    0: "temperature",           # 0.0=conservative, 1.0=creative
    1: "follow_up_cadence",     # 0.0=aggressive(1day), 1.0=gentle(7days)
    2: "opener_style",          # 0.0=empathy, 0.5=ROI, 1.0=question
    3: "objection_depth",       # 0.0=brief, 1.0=deep
    4: "closing_urgency",       # 0.0=soft, 1.0=hard close
    5: "tone_formality",        # 0.0=casual, 1.0=formal
    6: "content_density",       # 0.0=sparse, 1.0=rich detail
    7: "personalization_level"  # 0.0=generic, 1.0=hyper-personal
}
```

---

## ENVIRONMENT VARIABLES REFERENCE

```dotenv
# Sprint 2
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Sprint 3
POLYGON_API_KEY=...
FINNHUB_API_KEY=...
AGENTOPS_API_KEY=...
SERPER_API_KEY=...

# Sprint 4 — TransformerLens uses local models, no API key needed
```

---

## KNOWN GAPS (SPRINT 5+)

### Sprint 5 — COMPLETED ✅
| Task | File | Status |
|------|------|--------|
| S5-01 | `api/razorpay_webhook.py` + wire `main.py` | ✅ Built |
| S5-02 | `pods/aurora/rl_variants.py` — `retire_losing_variants()` + `get_active_variants()` | ✅ Built |
| S5-03 | `pods/syntropy_war_room/router.py` — SEAL endpoints `/session/start`, `/session/answer`, `/session/performance/{id}` | ✅ Built |
| S5-04 | `n8n/janus_to_aurora_regime_signal.json` | ✅ Built |
| S5-05 | `nexus-dashboard/dashboard.py` — Events page: 3s TTL, pod filter, color-coded feed, metrics row | ✅ Built |
| S5-06 | `nexus-dashboard/dashboard.py` — Evolution page: genome heatmap (Plasma, 8 genes, per-pod) | ✅ Built |
| S5-07 | `supabase/schema.sql` — `nexus_subscriptions` extended; `nexus_variant_stats`; `nexus_improvement_proofs` | ✅ Built |
| S5-08 | `tests/test_sprint5.py` — 14 tests covering all Sprint 5 modules | ✅ Built |

### Sprint 6 — Revenue Lock + Intelligence Upgrade (COMPLETED ✅)
| Task | File | Status |
|------|------|--------|
| S6-01 | `api/razorpay_webhook.py` — Redis retry queue, idempotency, dead-letter, Slack alert | ✅ Built |
| S6-02 | `pods/aurora/rl_variants.py` — `check_and_promote_champion()`, Vapi PATCH on 30+ calls / 60%+ win rate | ✅ Built |
| S6-03 | `pods/janus/alpaca_executor.py` — Alpaca paper trading, REGIME_ALLOCATION map, auto-wired to Janus router | ✅ Built |
| S6-04 | `core/improvement_proofs.py` — RSA-2048 `sign_proof_rsa()`, `verify_proof_rsa()`, `get_private_key()` | ✅ Built |
| S6-05 | `nexus-dashboard/dashboard.py` — Per-pod gene fitness drilldown (8-gene Plotly line chart) | ✅ Built |
| S6-06 | `core/interpretability.py` — `detect_pii_in_text()`, `detect_pii_attention_pattern()`, `verify_document_safety()` | ✅ Built |
| S6-07 | `api/realtime.py` — SSE `/api/realtime/events` endpoint, heartbeat every 30s | ✅ Built |
| S6-08 | `tests/test_sprint6.py` — 16 tests covering all Sprint 6 modules | ✅ Built |

### Sprint 7 — Live Intelligence + Cross-Pod Revenue (COMPLETED ✅)
| Task | File | Status |
|------|------|--------|
| S7-01 | `api/realtime.py` — `SupabaseRealtimeManager` class, `GET /api/realtime/health`, exponential backoff reconnect | ✅ Built |
| S7-02 | `tests/test_dan_graph.py` — 5 tests with correct patch targets (`core.ai_cascade.cascade_call`) | ✅ Built |
| S7-03 | `api/nexus.py` — `GET /api/nexus/variant-stats` endpoint + dashboard Champion vs Challenger A/B analytics tab | ✅ Built |
| S7-04 | `pods/syntropy_war_room/router.py` — ERS ≥ 75 cross-sell trigger + `n8n/syntropy_to_aurora_cross_sell.json` workflow | ✅ Built |
| S7-05 | `api/health.py` — full Sprint 7 coverage: realtime_ws, dan_graph, alpaca, rsa_signing, pii_detection, retry/dead-letter depths, variant_champions | ✅ Built |
| S7-06 | `tests/test_sprint7.py` — 12 tests; combined with DAN rewrite = **65/65 passing** | ✅ Built |

---

## EXECUTION ORDER

1. `S2-01` (genome decoder) — EVERYTHING ELSE DEPENDS ON THIS
2. `S2-02` (DAN graph) — revenue pod goes live
3. `S2-03 + S2-04` (Aurora brain) — booking rate lifts immediately
4. `S2-05` (Razorpay) — INR payments unblocked
5. `S2-06` (Slack alerts) — ops visibility
6. `S2-07 + S2-08` (dashboard + n8n) — cross-pod wired
7. `S3-01 → S3-04` in parallel
8. `S4-01 → S4-03` in parallel

---

*Shango India · team@shango.in · shango.in*
