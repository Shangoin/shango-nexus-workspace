#!/bin/bash
# scripts/validate_health.sh
# Boots the FastAPI app locally and hits /health to verify all systems.
# Usage: bash scripts/validate_health.sh
# Requires: Redis running locally on 6379

echo "▶ Starting nexus-backend locally for health check..."
cd "$(dirname "$0")/../nexus-backend"

export SUPABASE_URL="${SUPABASE_URL:-https://mock.supabase.co}"
export SUPABASE_KEY="${SUPABASE_KEY:-mock_key}"
export SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY:-mock_service_key}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export DISABLE_INTERPRETABILITY="1"
export ALPACA_ENABLED="false"
export GEMINI_API_KEY="${GEMINI_API_KEY:-mock}"
export SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-https://mock.slack.com}"

# Start backend in background
uvicorn main:app --host 127.0.0.1 --port 8765 &
SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# Wait for startup
echo "  Waiting for server to start..."
sleep 5

echo "▶ Hitting /health..."
HEALTH=$(curl -s http://127.0.0.1:8765/health || echo '{"error": "connection refused"}')
echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"

# Validate required fields
echo ""
echo "▶ Validating required fields..."
FIELDS=(
  "redis"
  "supabase"
  "realtime_ws"
  "dan_graph"
  "rsa_signing"
  "pii_detection"
  "retry_queue_depth"
  "dead_letter_depth"
  "variant_champions"
  "test_count"
  "version"
  "status"
)

ALL_GOOD=true
for field in "${FIELDS[@]}"; do
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert '$field' in d, '$field missing'" 2>/dev/null; then
    VALUE=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field', ''))" 2>/dev/null)
    echo "  ✅ $field = $VALUE"
  else
    echo "  ❌ $field — MISSING"
    ALL_GOOD=false
  fi
done

# Cleanup
kill $SERVER_PID 2>/dev/null || true

echo ""
if [ "$ALL_GOOD" = true ]; then
  echo "✅ Health validation PASSED — all fields present"
else
  echo "❌ Health validation FAILED — fix missing fields before deploy"
  exit 1
fi
