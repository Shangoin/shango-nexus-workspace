# /new-pod — Scaffold a new Nexus pod

Argument: `$ARGUMENTS` = the pod name (snake_case, e.g. "content_engine" or "pricing_oracle")

Execute the following steps in order.

## Step 1 — Parse and validate

Extract the pod name from `$ARGUMENTS`. If no argument is provided, ask the user:
"What should the new pod be named? (snake_case, e.g. content_engine)"

Confirm:
- Pod name is snake_case
- No existing pod has this name (check `nexus-backend/pods/` directory)
- Derive the route prefix: `/api/{pod_name}` with hyphens (e.g. `content_engine` → `/api/content-engine`)

## Step 2 — Read reference files

Before writing any code, read these to understand the pattern:
- `nexus-backend/pods/aurora/router.py` — full pod router example
- `nexus-backend/core/evolution.py` lines 44–49 — `register_pod()` pattern
- `nexus-backend/main.py` — how routers are mounted

## Step 3 — Create pod files

Create the following files:

### `nexus-backend/pods/{pod_name}/__init__.py`
```python
"""
{pod_name} pod — scaffolded by /new-pod command.
Registers pod with the DEAP evolution engine on import.
"""
from pods.{pod_name}.router import router
```

### `nexus-backend/pods/{pod_name}/router.py`

Full router with:
- `RunRequest` Pydantic model (task: str, plus 2–3 domain-specific fields)
- `RunResponse` Pydantic model (pod, result, score, verified, elapsed_ms)
- `POST /run` endpoint that:
  1. Checks constitution via `validate()`
  2. Calls `cascade_call()` for primary intelligence
  3. Uses `pacv_loop()` for verification if task is multi-step
  4. Publishes `{pod_name}.task_completed` event via `publish()`
  5. Returns `RunResponse`
- `GET /status` endpoint returning pod name + fitness + event count
- `_fitness(individual)` async function returning float 0.0–1.0
- `register_pod("{pod_name}", _fitness)` call at module level

The fitness function must use `mae_adversarial_fitness` from `core.evolution` as its base:
```python
import functools
from core.evolution import mae_adversarial_fitness, register_pod

_mae_fitness = functools.partial(mae_adversarial_fitness, pod="{pod_name}")

async def _fitness(individual: list) -> float:
    return await _mae_fitness(individual)

register_pod("{pod_name}", _fitness)
```

## Step 4 — Wire the router into main.py

Read `nexus-backend/main.py` and add:
```python
from pods.{pod_name}.router import router as {pod_name}_router
app.include_router({pod_name}_router, prefix="/api/{route_prefix}", tags=["{pod_name}"])
```

Place it alphabetically among the other pod router includes.

## Step 5 — Add genome decoder support

Read `nexus-backend/core/genome_decoder.py`.

In `decode_genome()`, add a new `elif pod_name == "{pod_name}":` block that maps the 8 genes to pod-specific parameter names. Use domain-appropriate names for this pod.

## Step 6 — Write tests

Create `nexus-backend/tests/test_{pod_name}.py` with at least 8 tests:

1. `test_run_endpoint_exists` — POST /run returns 200
2. `test_run_returns_result_field` — response has "result" key
3. `test_run_constitution_check` — constitution is called
4. `test_fitness_returns_float` — `_fitness([0.5]*8)` returns float 0–1
5. `test_fitness_fail_open` — fitness returns 0.5 when cascade raises
6. `test_status_endpoint` — GET /status returns 200 with pod name
7. `test_register_pod_called` — pod name appears in `POD_FITNESS_FNS` registry
8. `test_genome_decoder_support` — `decode_genome([0.5]*8, "{pod_name}")` returns dict

All external calls (cascade, supabase, publish) must be `@patch`ed with `AsyncMock`.

## Step 7 — Update CLAUDE.md pod registry

Add the new pod to the Pod Registry table in `CLAUDE.md`:
```
| `{pod_name}` | `/api/{route_prefix}` | 30% | TBD |
```

## Step 8 — Summary

List all files created or modified. State:
"Pod `{pod_name}` scaffolded. Ready for domain logic. Next step: implement the core intelligence in `router.py` POST /run."
