# /health-check — Validate Nexus system health and surface issues

Perform a deep health check of the Shango Nexus codebase. This is a read-only analysis — no files are modified.

Report format: a structured table for each category, with a clear PASS / WARN / FAIL status per item.

## Check 1 — Architecture compliance

Read each file listed below and verify compliance with CLAUDE.md rules:

| Rule | What to check | Files to spot-check |
|------|---------------|---------------------|
| No direct LLM calls in pods | No pod router imports `google.generativeai`, `groq`, `openai` directly | 3 random pod routers |
| Constitution check in all pods | Every POST endpoint calls `validate()` or the router has a comment explaining why it's exempt | `aurora/router.py`, `dan/router.py`, 1 more |
| All pods registered | `register_pod()` is called in every pod that has a fitness function | Check `evolution.py` `POD_FITNESS_FNS` |
| No hardcoded secrets | No `sk-`, `AIza`, `Bearer ` literals in any `.py` file | Grep `nexus-backend/` |
| Async Supabase calls | All Supabase calls in async functions use `asyncio.to_thread()` | Spot-check 2 routers |

Report as: ✅ PASS, ⚠️ WARN (minor), or ❌ FAIL (breaking)

## Check 2 — Test coverage health

For each test file, read the first 30 lines (header + first test class) and record:

| File | Sprint | Test count (from header) | Status |
|------|--------|--------------------------|--------|
| test_core.py | Core | ? | ? |
| test_sprint5.py | 5 | ? | ? |
| ... | ... | ? | ? |

Sum total tests. Compare to `health.py` `test_count` field. Flag if mismatched.

## Check 3 — Constitution integrity

Read `nexus-backend/constitution.yaml` in full.

Verify:
- All 6 rules are present: `no_pii_storage`, `no_financial_advice`, `no_harmful_content`, `no_ai_speak`, `max_prompt_tokens`, `rate_limit_ai`
- All 4 circuit breakers are present: `ai_cascade`, `supabase`, `evolution_cycle`, `vapi`
- `max_prompt_tokens` is 32,000 or higher
- `rate_limit_ai` is 100/60s or more restrictive

## Check 4 — MCP adapter coverage

Read `nexus-backend/core/mcp_adapter.py`.

Verify:
- All 7 core tools are registered: `supabase_query`, `supabase_insert`, `supabase_upsert`, `redis_get`, `redis_set`, `cascade`, `publish_event`
- `list_tools()` returns MCP-spec dicts
- `MCPToolError` is defined
- `mcp_call()` logs timing and errors

## Check 5 — Cross-pod signal map

Read `nexus-backend/events/bus.py`.

Verify the 5 cross-pod routes are wired:
- `aurora.booking_failed` → `syntropy.generate_resource`
- `aurora.booking_failed` → `janus.analyze_objection`
- `janus.regime_change` → `aurora.trigger_upsell`
- `dan.incident_detected` → `sentinel_prime.analyze_incident`
- `syntropy.quiz_completed` → `ralph.update_prd`

## Check 6 — Health endpoint

Read `nexus-backend/api/health.py`.

Verify the endpoint reports:
- `test_count` matches the actual test total from Check 2
- `version` reflects the current sprint
- `coordination_efficiency` is present (Sprint 11 addition)
- `scaling_healthy` is present

## Check 7 — CLAUDE.md freshness

Read `CLAUDE.md`.

Check:
- Sprint number matches current sprint
- Test count matches actual count
- All 13 pods are in the Pod Registry table
- Research implementations table includes Sprints up to current

## Final report

Output a single summary table:

| Category | Status | Issues Found |
|----------|--------|--------------|
| Architecture compliance | ✅/⚠️/❌ | ... |
| Test coverage | ✅/⚠️/❌ | ... |
| Constitution integrity | ✅/⚠️/❌ | ... |
| MCP adapter coverage | ✅/⚠️/❌ | ... |
| Cross-pod signals | ✅/⚠️/❌ | ... |
| Health endpoint | ✅/⚠️/❌ | ... |
| CLAUDE.md freshness | ✅/⚠️/❌ | ... |

Then list all ❌ FAIL and ⚠️ WARN items with a one-line fix description for each.

End with: "Overall system health: HEALTHY / DEGRADED / CRITICAL"
