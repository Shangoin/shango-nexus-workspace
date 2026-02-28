# Shango Nexus v6.0

**Alien Intelligence HQ for Shango India** · [shango.in](https://shango.in) · team@shango.in

Shango Nexus fuses 13 AI projects into one self-evolving system: DEAP genetic evolution, 6-LLM cascade, pgvector semantic memory, and constitutional constraints — deployed multi-region from Singapore + Oregon on Render, with a Vercel edge landing on `sin1`+`iad1`.

> Full design rationale, before/after breakdown, and sprint history: [NEXUS_DESIGN_EVOLUTION.md](NEXUS_DESIGN_EVOLUTION.md)

---

## Architecture

```
shango.in (Vercel sin1+iad1)        nexus-backend-sg (Render Singapore)
landing/                    ─┬────▶  nexus-backend-us (Render Oregon)
  src/app/page.tsx           │         main.py (FastAPI lifespan)
  src/lib/api.ts ◀──region?──┘         core/
                                          ai_cascade.py   ← 6-LLM cascade
                                          memory.py       ← Redis→pgvector→mem0
                                          evolution.py    ← DEAP genetics
                                          constitution.py ← YAML rules + breakers
                                          mcts_graph.py   ← LangGraph PACV
                                        events/bus.py     ← Supabase realtime
                                        pods/             ← 13 subdirs

nexus-dashboard (Render Singapore)
  nexus-dashboard/dashboard.py (Streamlit)
```

## Quick Start

```powershell
# 1. Clone and setup
cd shango-nexus-workspace
cp nexus-backend/.env.example nexus-backend/.env
# Edit .env with your API keys

# 2. Backend
cd nexus-backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# → http://localhost:8000/health
# → http://localhost:8000/docs

# 3. Dashboard
cd nexus-dashboard
streamlit run dashboard.py
# → http://localhost:8501

# 4. Landing
cd landing
npm install && npm run dev
# → http://localhost:3000

# 5. Full stack via Docker
docker compose up -d --build
```

## Deployment

### Render (Backend + Dashboard — multi-region)
```bash
# One-click: Render Dashboard → New → Blueprint → connect this repo
# render.yaml provisions:
#   nexus-backend-sg  (Singapore, 2 workers, auto-scale 1→3)
#   nexus-backend-us  (Oregon,    2 workers, auto-scale 1→3)
#   nexus-dashboard   (Singapore, Streamlit)
#   nexus-redis       (Singapore, starter, allkeys-lru)
```

### Vercel (Landing — edge-global)
```bash
cd landing
vercel --prod
# vercel.json deploys to sin1 (Singapore) + iad1 (US East)
# Env vars to set in Vercel:
#   NEXT_PUBLIC_API_BASE_SG = https://nexus-backend-sg.onrender.com
#   NEXT_PUBLIC_API_BASE_US = https://nexus-backend-us.onrender.com
```

### Supabase
```sql
-- Run supabase/schema.sql in Supabase SQL Editor
-- Enable Realtime on: nexus_events, nexus_evolutions
```

## Test Suite (73/73)

| File | Tests | Coverage |
|------|-------|---------|
| `tests/test_core.py` | 12 | Core: cascade, memory, evolution, constitution |
| `tests/test_sprint5.py` | 14 | Razorpay, SEAL, n8n wiring, schema |
| `tests/test_sprint6.py` | 22 | Retry queue, RSA proofs, Alpaca, SSE, PII |
| `tests/test_dan_graph.py` | 5 | DAN LangGraph state machine |
| `tests/test_sprint7.py` | 12 | Realtime, variant-stats, cross-sell, health v6 |
| `tests/test_sprint8.py` | 8 | Multi-region config, Vercel, CI, api.ts |

```powershell
# Run all tests
cd nexus-backend
..\.venv\Scripts\pytest.exe tests/ -v --tb=short

# Or use the E2E script
bash scripts/run_all_tests.sh
```

## Pods (13 Prometheus Organs)

| Pod | Role | Revenue | Completion |
|-----|------|---------|------------|
| aurora | Sales Organ | $99/mo | 70% |
| janus | Trading Brain | 1% AUM | 75% |
| dan | IT Swarm | $49/mo | 65% |
| syntropy | Tutor Organ | $29/pack | 85% |
| ralph | PRD Forge | Internal | 95% |
| sentinel_prime | Doc Intel | $199/mo | 80% |
| sentinel_researcher | Research Eye | Internal | 45% |
| shango_automation | Webhook Veins | $19/mo | 90% |
| syntropy_lite | KG Brain | Internal | 70% |
| syntropy_war_room | Exam Arena | Bundle | 85% |
| syntropy_scaffold | Launch Pad | Internal | 75% |
| viral_music | Creative Limb | Affiliate | 85% |
| syntropy_launch | Deployer | Internal | 95% |

## Core Systems

### AI Cascade (6-LLM)
```python
from core.ai_cascade import cascade_call
result = await cascade_call(prompt, task_type="scoring", pod_name="aurora")
# tries: gemini-2.5-flash → groq-llama3.3-70b → cerebras → mistral → deepseek → gpt-4o-mini
```

### DEAP Evolution
```python
from core.evolution import register_pod, genetic_cycle
register_pod("my_pod", fitness_fn=my_async_fitness)
result = await genetic_cycle("my_pod", supabase_client)
```

### PACV Loop (Plan→Act→Critique→Verify)
```python
from core.mcts_graph import pacv_loop
state = await pacv_loop(goal="Write a sales email", ai_fn=cascade_call)
```

### Event Bus
```python
from events.bus import NexusEvent, publish
await publish(NexusEvent("aurora", "booking_won", {"lead_id": "..."}), supabase)
```

### Region-Aware API Client (landing)
```ts
import { apiFetch } from "@/lib/api";
const data = await apiFetch<{ status: string }>("/api/nexus/variant-stats");
// Automatically routes to SG or US backend based on visitor hostname
```

## Revenue Architecture

| Stream | Pod | Trigger | Monthly Target |
|--------|-----|---------|----------------|
| Voice sales calls | aurora | Lead capture → Vapi ARIA | ₹40k |
| Exam packs | syntropy | Student buys study pack | ₹25k |
| IT consulting | dan | LangGraph diagnose+fix | ₹15k |
| Trading signals | janus | Alpaca paper → live AUM% | ₹10k |
| Automation webhooks | shango_automation | n8n workflow fire | ₹5k |

## Success Metrics

- Aurora: >20% booking rate
- Evolution: score_after > score_before (RSA-signed proof)
- Cross-pod: ERS ≥ 75 auto-triggers Aurora cross-sell without human SDR
- Latency: <50ms to nearest region (SG or US)
- Revenue: $1k MRR by Week 4

## Contact

team@shango.in · Kolkata, India · Zero new hires. n8n for support.
