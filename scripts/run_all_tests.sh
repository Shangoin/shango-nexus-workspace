#!/bin/bash
# scripts/run_all_tests.sh
# Run the full Shango Nexus test suite across all sprints.
# Usage: bash scripts/run_all_tests.sh
# Expected: 73/73 PASS (sprint files only — pre-existing test files may vary)

set -e

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   SHANGO NEXUS — FULL E2E TEST SUITE                ║"
echo "║   Sprint 1–8 · 73 sprint tests · Feb 28 2026        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")/../nexus-backend"

# ── Environment (safe mock values for CI) ─────────────────────────────────────
export SUPABASE_URL="${SUPABASE_URL:-https://mock.supabase.co}"
export SUPABASE_KEY="${SUPABASE_KEY:-mock_key_for_ci}"
export SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY:-mock_service_key}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export DISABLE_INTERPRETABILITY="1"
export ALPACA_ENABLED="false"
export SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-https://mock.slack.com}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-mock_gemini}"
export GROQ_API_KEY="${GROQ_API_KEY:-mock_groq}"
export VAPI_API_KEY="${VAPI_API_KEY:-mock_vapi}"
export VAPI_ASSISTANT_ID="${VAPI_ASSISTANT_ID:-mock_asst}"
export RAZORPAY_KEY_ID="${RAZORPAY_KEY_ID:-mock_rzp}"
export RAZORPAY_KEY_SECRET="${RAZORPAY_KEY_SECRET:-mock_rzp_secret}"
export RAZORPAY_WEBHOOK_SECRET="${RAZORPAY_WEBHOOK_SECRET:-mock_webhook_secret}"
export STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-mock_stripe}"
export BREVO_API_KEY="${BREVO_API_KEY:-mock_brevo}"
export SERPER_API_KEY="${SERPER_API_KEY:-mock_serper}"
export POLYGON_API_KEY="${POLYGON_API_KEY:-mock_polygon}"
export N8N_URL="${N8N_URL:-https://mock.n8n.test}"

# ── Sprint test files (the 73 verified tests) ─────────────────────────────────
SPRINT_FILES="tests/test_core.py tests/test_sprint5.py tests/test_sprint6.py tests/test_sprint7.py tests/test_dan_graph.py tests/test_sprint8.py"

echo "▶ Running: Core tests (Sprint 1)"
python -m pytest tests/test_core.py -v --tb=short
echo ""

echo "▶ Running: Sprint 5 — Revenue & Realtime"
python -m pytest tests/test_sprint5.py -v --tb=short
echo ""

echo "▶ Running: Sprint 6 — Revenue Lock + Intelligence"
python -m pytest tests/test_sprint6.py -v --tb=short
echo ""

echo "▶ Running: Sprint 7 — Realtime WS + DAN CI + A/B + Cross-sell"
python -m pytest tests/test_sprint7.py -v --tb=short
echo ""

echo "▶ Running: DAN LangGraph CI tests"
python -m pytest tests/test_dan_graph.py -v --tb=short
echo ""

echo "▶ Running: Sprint 8 — Multi-region Deploy"
python -m pytest tests/test_sprint8.py -v --tb=short
echo ""

echo "══════════════════════════════════════════════════════"
echo "▶ Running FULL SPRINT SUITE with coverage report..."
python -m pytest $SPRINT_FILES \
  -v \
  --tb=short \
  --asyncio-mode=auto \
  --cov=. \
  --cov-report=term-missing \
  2>&1 | tee ../test_results_full.txt

PASS_LINE=$(grep -E "[0-9]+ passed" ../test_results_full.txt | tail -1)
FAIL_COUNT=$(echo "$PASS_LINE" | grep -oE "[0-9]+ failed" | grep -oE "^[0-9]+" || echo "0")
PASS_COUNT=$(echo "$PASS_LINE" | grep -oE "^[0-9]+ passed" | grep -oE "^[0-9]+" || echo "?")

echo ""
echo "╔══════════════════════════════════════════════════════╗"
if [ "$FAIL_COUNT" = "0" ] || [ -z "$FAIL_COUNT" ]; then
  echo "║  ✅ ${PASS_COUNT} SPRINT TESTS PASSED — READY TO DEPLOY    ║"
else
  echo "║  ❌ ${FAIL_COUNT} TESTS FAILED — DO NOT DEPLOY              ║"
fi
echo "╚══════════════════════════════════════════════════════╝"
echo ""

if [ "$FAIL_COUNT" != "0" ] && [ -n "$FAIL_COUNT" ]; then
  exit 1
fi
