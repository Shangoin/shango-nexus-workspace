# /scan — Full disk scan and document update

Scan the entire `shango-nexus-workspace/` directory and produce verified, accurate updates to all project documentation.

This command replaces stale assumptions with confirmed facts. Every claim in the output docs must be verifiable from an actual file read.

## Step 1 — Glob the full workspace

Run a recursive glob on `nexus-backend/` to get all `.py` files. Also glob:
- `n8n/*.json`
- `landing/src/**/*.tsx`
- `nexus-dashboard/*.py`
- `supabase/*.sql`
- `*.yml` and `*.yaml`
- `scripts/*.sh`

## Step 2 — Read and verify core files

Read every file in this list and note: (a) whether it is empty/stub, (b) the key functions/classes it exports, (c) any obvious gaps or TODOs:

**Core infrastructure:**
- `nexus-backend/core/ai_cascade.py`
- `nexus-backend/core/evolution.py`
- `nexus-backend/core/constitution.py`
- `nexus-backend/core/memory.py`
- `nexus-backend/core/mcts_graph.py`
- `nexus-backend/core/genome_decoder.py`
- `nexus-backend/core/mcp_adapter.py`
- `nexus-backend/core/agent_scaling_monitor.py`
- `nexus-backend/core/encompass.py`
- `nexus-backend/core/mem1_state.py`

**Key pod routers (sample):**
- `nexus-backend/pods/aurora/router.py`
- `nexus-backend/pods/janus/router.py`
- `nexus-backend/pods/dan/router.py`
- `nexus-backend/pods/syntropy_war_room/router.py`

**APIs and infra:**
- `nexus-backend/api/health.py`
- `nexus-backend/api/payments.py`
- `nexus-backend/main.py`
- `docker-compose.yml`
- `nexus-backend/constitution.yaml`

**Tests (headers only — read first 20 lines of each):**
- Every file matching `nexus-backend/tests/test_*.py`

## Step 3 — Count verified items

From your reads, count:
- Total `.py` files in `nexus-backend/`
- Total non-empty pod router files
- Total test files
- Total test count (sum up the "Target: N tests" comments from each test file header)
- Empty `__init__.py` stubs (list by pod name)

## Step 4 — Identify discrepancies

Compare what you found in Step 2–3 against any stale values in:
- `PROJECT_STATUS.md`
- `CLAUDE.md` (test count, sprint number)
- `nexus-backend/api/health.py` (test_count field, version field)

Build a discrepancy table:
| File | Stale Value | Correct Value |
|------|-------------|---------------|

## Step 5 — Update PROJECT_STATUS.md

Rewrite with:
- Today's date at the top
- Every item tagged as `VERIFIED (read)` or `File present` based on your actual reads
- Scan Notes section with the discrepancy table from Step 4
- Correct test count
- Updated sprint number

## Step 6 — Update NEXUS_DESIGN_EVOLUTION.md

Make targeted edits (do not rewrite the entire file — it is large):
1. Update the version/date header block (lines 1–8)
2. Append a new "Scan Verification" section at the bottom with:
   - Date of scan
   - Files verified table
   - Empty stubs table
   - Discrepancies found
   - Top 5 next actions

## Step 7 — Update CLAUDE.md

Fix any stale values:
- Current sprint number
- Test count
- Any pod completion percentages that are clearly outdated

## Step 8 — Summary

State:
- Date of scan
- Total files read
- Discrepancies found and fixed
- Documents updated
