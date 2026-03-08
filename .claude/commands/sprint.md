# /sprint — Build the next Shango Nexus sprint

Execute the following steps in order. Do not skip steps.

## Step 1 — Read current state

Read these files to understand where we are:
- `PROJECT_STATUS.md` — current sprint number, what's complete, what's blocked
- `NEXUS_DESIGN_EVOLUTION.md` lines 1–15 (version/date header only)
- `nexus-backend/tests/` — list all test files to find the latest sprint number

From PROJECT_STATUS.md, identify:
1. The current sprint number (call it N)
2. The next sprint number (N+1)
3. The first 2–3 incomplete items from "Known Gaps and Next Steps" or any items marked with ⚠️

## Step 2 — Plan the sprint

Based on what you read, define 3–5 features for Sprint N+1. Each feature must:
- Have a clear S{N+1}-{num} tag (e.g. S12-00, S12-01)
- Relate to a specific file to create or modify
- Have a test target (how many tests it will add)
- Require no new API keys (unless the user specifies one in $ARGUMENTS)

If the user passed arguments via `$ARGUMENTS`, incorporate their specific request as the primary feature.

Announce the sprint plan before writing any code.

## Step 3 — Implement each feature

For each feature in the sprint:
1. Read the relevant existing file(s) before editing
2. Write the minimal code that implements the feature
3. Follow CLAUDE.md rules:
   - Use `cascade_call` for all LLM calls, never direct API calls
   - Use `mcp_call` for Supabase/Redis in new code
   - Async everywhere
   - Pydantic models for all request shapes
   - Fail-open: catch exceptions, log warning, return default
4. Tag every new function/class with its sprint feature tag in the docstring

## Step 4 — Write tests

Create `nexus-backend/tests/test_sprint{N+1}.py` with:
- Header docstring listing all features covered
- Target: at least 16 tests (aim for 20–24)
- One test class per feature (e.g. `class TestMCPAdapter:`)
- Use `unittest.mock.AsyncMock` for all LLM and Supabase calls
- All tests must pass without real API keys (`@patch` everything external)
- Final comment: `# Target: {count} tests → cumulative {total} across all sprints`

## Step 5 — Update documentation

1. **`PROJECT_STATUS.md`**: Update the date, add Sprint N+1 items to "What Is Completely Built", move resolved gaps from "Known Gaps" to "Resolved"

2. **`NEXUS_DESIGN_EVOLUTION.md`**:
   - Update version to {N+1}.0 in the header
   - Update date
   - Add a new "Sprint N+1" subsection under Section 3 ("What Nexus Added") listing all new files/functions
   - Add a Sprint N+1 row to the per-pod upgrade table in Section 8 if applicable
   - Add resolved gaps to the appropriate "Resolved in Sprint N+1" table in Section 10

3. **`CLAUDE.md`**:
   - Update "Current sprint" to N+1
   - Update "Test count" to new total
   - Add new research implementations to the table if applicable

## Step 6 — Verify

List all files created or modified. Count the tests added. Confirm the cumulative test total.

State clearly: "Sprint {N+1} complete. {count} tests added. Cumulative: {total} tests."
