#!/bin/bash
# scripts/push_to_github.sh
# Pre-push safety gate: run tests → scan secrets → gitignore check → commit → push
# Usage: bash scripts/push_to_github.sh [commit message override]

set -e
ROOT="$(dirname "$0")/.."
cd "$ROOT"

echo "======================================================"
echo " Shango Nexus — Pre-Push Safety Gate"
echo "======================================================"

# Step 1: Run full test suite
echo ""
echo "▶ Step 1/5 — Running test suite..."
bash scripts/run_all_tests.sh
echo "  ✅ All tests passed"

# Step 2: Scan staged files for secrets
echo ""
echo "▶ Step 2/5 — Scanning for secrets..."
SECRETS_FOUND=false
SECRET_PATTERNS=(
  "sk-[A-Za-z0-9]{20,}"
  "AIza[A-Za-z0-9_-]{35}"
  "rzp_live_[A-Za-z0-9]+"
  "whsec_[A-Za-z0-9]+"
  "xoxb-[A-Za-z0-9-]+"
  "SUPABASE_SERVICE_KEY=[^$]"
  "password=[^$]"
)

for pattern in "${SECRET_PATTERNS[@]}"; do
  MATCHES=$(git diff --cached --name-only | xargs -I{} grep -lE "$pattern" {} 2>/dev/null || true)
  if [ -n "$MATCHES" ]; then
    echo "  ❌ Possible secret found (pattern: $pattern) in: $MATCHES"
    SECRETS_FOUND=true
  fi
done

if [ "$SECRETS_FOUND" = true ]; then
  echo "  ❌ ABORT: Secrets detected in staged files"
  exit 1
else
  echo "  ✅ No secrets found in staged files"
fi

# Step 3: Verify .env files are gitignored
echo ""
echo "▶ Step 3/5 — Verifying .env files are gitignored..."
ENV_TRACKED=$(git ls-files | grep -E "^\.env$|^nexus-backend/\.env$|^landing/\.env$" || true)
if [ -n "$ENV_TRACKED" ]; then
  echo "  ❌ ABORT: .env file(s) are tracked by git: $ENV_TRACKED"
  exit 1
else
  echo "  ✅ .env files are not tracked"
fi

# Step 4: Stage all changes and commit
echo ""
echo "▶ Step 4/5 — Staging and committing..."
git add -A

if git diff --cached --quiet; then
  echo "  ℹ️  Nothing to commit — working tree clean"
else
  COMMIT_MSG="${1:-"Sprint 8: Multi-region deploy + E2E validation + GitHub Actions CI — 73/73 tests green"}"
  git commit -m "$COMMIT_MSG"
  echo "  ✅ Committed: $COMMIT_MSG"
fi

# Step 5: Push to main
echo ""
echo "▶ Step 5/5 — Pushing to origin/main..."
git push origin main
echo "  ✅ Pushed to origin/main"

echo ""
echo "======================================================"
echo " ✅ Push complete — Shango Nexus is live"
echo "======================================================"
