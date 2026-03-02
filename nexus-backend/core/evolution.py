"""
nexus/core/evolution.py
MARS genetic evolution engine (DEAP-based).
Each pod exposes a fitness function via POD_FITNESS_FNS registry.
Every CYCLE_THRESHOLD events, genetic_cycle() runs for all active pods.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import random
import re
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine

import numpy as np
from deap import algorithms, base, creator, tools  # type: ignore

logger = logging.getLogger(__name__)

CYCLE_THRESHOLD = 25          # events per pod before evolution triggers
POPULATION_SIZE = 50
GENERATIONS = 10
MUTATION_PB = 0.2
CROSSOVER_PB = 0.5
TOURNAMENT_SIZE = 3

# ── DEAP types (created once) ────────────────────────────────────────────────
if not hasattr(creator, "FitnessMax"):
    creator.create("FitnessMax", base.Fitness, weights=(1.0,))
if not hasattr(creator, "Individual"):
    creator.create("Individual", list, fitness=creator.FitnessMax)

# ── Pod-level registries ─────────────────────────────────────────────────────
# Each pod registers:  pod_name → async fn(individual) → float  (fitness)
POD_FITNESS_FNS: dict[str, Callable[..., Coroutine]] = {}
POD_EVENT_COUNTERS: dict[str, int] = {}


def register_pod(pod_name: str, fitness_fn: Callable) -> None:
    """Pods call this at startup to register their fitness evaluator."""
    POD_FITNESS_FNS[pod_name] = fitness_fn
    POD_EVENT_COUNTERS[pod_name] = 0
    logger.info("[evolution] registered pod=%s", pod_name)


# ── MAE helpers ───────────────────────────────────────────────────────────────

def _parse_json_safe_mae(text: str) -> dict:
    """Extract first JSON object from LLM text response."""
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else {}
    except Exception:
        return {}


# ── S10-03: Agent0 curriculum — uncertainty-driven challenge adaptation ——————————

_uncertainty_history: dict[str, list[float]] = defaultdict(list)


def record_mae_score(pod: str, judge_score: float) -> None:
    """
    Purpose:  Record per-pod judge scores to build uncertainty estimate.
    Inputs:   pod str, judge_score float 0.0-1.0
    Outputs:  None (mutates _uncertainty_history)
    Side Effects: Keeps last 20 scores per pod.
    """
    history = _uncertainty_history[pod]
    history.append(float(judge_score))
    if len(history) > 20:
        _uncertainty_history[pod] = history[-20:]


def get_executor_uncertainty(pod: str) -> float:
    """
    Purpose:  Compute uncertainty as 4x variance of last 10 scores, capped at 1.0.
              High variance → high uncertainty → harder challenges.
    Inputs:   pod str
    Outputs:  float 0.0-1.0
    Side Effects: None (read-only)
    """
    history = _uncertainty_history.get(pod, [])
    window = history[-10:]
    if len(window) < 2:
        return 0.5  # Assume medium uncertainty on first runs
    variance = float(np.var(window))
    return min(1.0, variance * 4.0)


async def curriculum_guided_challenge(pod: str, genome_summary: str, uncertainty: float) -> str:
    """
    Purpose:  Generate a challenge calibrated to agent uncertainty (Agent0 curriculum schedule).
              Low uncertainty → hard challenge; High uncertainty → easier scaffold first.
    Inputs:   pod str, genome_summary str, uncertainty float 0.0-1.0
    Outputs:  challenge str
    Side Effects: 1 cascade_call
    """
    from core.ai_cascade import cascade_call

    if uncertainty < 0.3:
        # Agent is consistent → challenge hard
        difficulty_hint = "Make the challenge HARD and adversarial. Probe edge cases and failure modes."
    elif uncertainty < 0.6:
        # Medium uncertainty → moderate challenge
        difficulty_hint = "Make it moderately difficult. Include one tricky constraint."
    else:
        # High variance → scaffold with a clear, structured challenge
        difficulty_hint = "Make it structured and clear. The agent is still learning this skill."

    try:
        raw = await cascade_call(
            f"You are a Curriculum Designer for an AI agent ({pod} pod). "
            f"Genome config: {genome_summary}. "
            f"Agent uncertainty: {uncertainty:.2f}. "
            f"{difficulty_hint} "
            f"Output ONE challenge (2-3 sentences). No commentary.",
            task_type="mae_curriculum",
            pod_name=pod,
        )
        return raw.strip()
    except Exception as exc:
        logger.warning("[evolution.curriculum] fail pod=%s: %s", pod, exc)
        return "Handle a complex multi-step edge case with ambiguous inputs."


async def mae_adversarial_fitness(individual: list, pod: str) -> float:
    """
    Multi-Agent Evolve + Agent0 Curriculum (S10-03): Proposer-Solver-Judge fitness
    with uncertainty-adaptive challenge difficulty.
    Purpose:  Adversarial triad evaluates an individual's genome quality.
              Challenge difficulty scales with executor uncertainty (Agent0 curriculum).
    Inputs:   individual (list of 8 floats), pod (str)
    Outputs:  float fitness score 0.0-1.0 (+0.2 bonus if judge_score < 0.4)
    Side Effects: 3-4 LLM cascade calls; records score to _uncertainty_history
    """
    from core.ai_cascade import cascade_call
    from core.genome_decoder import decode_genome

    try:
        genome_config = decode_genome(list(individual), pod)

        # S10-03: Agent0 — use uncertainty to calibrate challenge difficulty
        uncertainty = get_executor_uncertainty(pod)
        challenge = await curriculum_guided_challenge(pod, str(genome_config), uncertainty)

        # Steps 2 + 3 — SOLVER and JUDGE in parallel
        solver_prompt = (
            f"You are a Solver. Handle this challenge using config: {genome_config}. "
            f"Challenge: {challenge}. "
            f'Output JSON: {{"solution": "str", "confidence": 0.7}}'
        )
        judge_initial_prompt = (
            f"You are a Judge evaluating an AI solver\u2019s response. "
            f"Challenge: {challenge}. Genome config: {genome_config}. "
            f"Rate how well a typical solver would handle this. "
            f'Output JSON: {{"score": 0.5, "reasoning": "str"}}'
        )

        solver_raw, judge_initial_raw = await asyncio.gather(
            cascade_call(solver_prompt, task_type="mae_solver", pod_name=pod),
            cascade_call(judge_initial_prompt, task_type="mae_judge", pod_name=pod),
        )

        solver = _parse_json_safe_mae(solver_raw)
        judge = _parse_json_safe_mae(judge_initial_raw)
        judge_score = float(judge.get("score", 0.5))

        # Record for next-cycle uncertainty estimate
        record_mae_score(pod, judge_score)

        # Difficulty reward: proposer rewarded for stumping the solver
        if judge_score < 0.4:
            return judge_score + 0.2
        return judge_score

    except Exception as exc:
        logger.warning("[evolution.mae] fail pod=%s err=%s", pod, exc)
        return 0.5  # fail-open — never crash


def increment_event(pod_name: str) -> bool:
    """Returns True when threshold is crossed (caller should trigger cycle)."""
    POD_EVENT_COUNTERS[pod_name] = POD_EVENT_COUNTERS.get(pod_name, 0) + 1
    return POD_EVENT_COUNTERS[pod_name] % CYCLE_THRESHOLD == 0


# ── Individual representation ─────────────────────────────────────────────────
# An individual is a list of floats [0, 1] (prompt-parameter weights).
# Pods decode these into concrete prompt mutations in their fitness fn.

def _make_individual(dim: int = 8) -> creator.Individual:
    return creator.Individual(random.uniform(0, 1) for _ in range(dim))


def _mutate_individual(ind: creator.Individual, mu: float = 0.0, sigma: float = 0.15) -> tuple:
    tools.mutGaussian(ind, mu=mu, sigma=sigma, indpb=0.3)
    # Clamp to [0, 1]
    for i in range(len(ind)):
        ind[i] = max(0.0, min(1.0, ind[i]))
    return (ind,)


async def _evaluate(individual: creator.Individual, fitness_fn: Callable) -> tuple[float]:
    try:
        score = await fitness_fn(individual)
        return (float(score),)
    except Exception as exc:
        logger.warning("[evolution] eval fail: %s", exc)
        return (0.0,)


async def genetic_cycle(pod_name: str, supabase_client=None) -> dict:
    """
    Run one DEAP evolutionary cycle for a pod.
    Returns best individual + score, persists to nexus_evolutions table.
    """
    fitness_fn = POD_FITNESS_FNS.get(pod_name)
    if fitness_fn is None:
        return {"error": f"pod {pod_name} has no fitness function registered"}

    # ── MAE adversarial fitness (S9-01) ─────────────────────────────────────
    mae_enabled = os.getenv("MAE_ADVERSARIAL", "true").lower() == "true"
    if mae_enabled and fitness_fn is not None:
        mae_fn = functools.partial(mae_adversarial_fitness, pod=pod_name)
        effective_fitness_fn = mae_fn
    else:
        effective_fitness_fn = fitness_fn

    toolbox = base.Toolbox()
    toolbox.register("individual", _make_individual)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("evaluate", _evaluate, fitness_fn=effective_fitness_fn)
    toolbox.register("mate", tools.cxBlend, alpha=0.5)
    toolbox.register("mutate", _mutate_individual)
    toolbox.register("select", tools.selTournament, tournsize=TOURNAMENT_SIZE)

    pop = toolbox.population(n=POPULATION_SIZE)
    hof = tools.HallOfFame(1)
    stats = tools.Statistics(lambda ind: ind.fitness.values)
    stats.register("max", np.max)
    stats.register("avg", np.mean)

    # Evaluate initial population
    fitnesses = await asyncio.gather(*[toolbox.evaluate(ind) for ind in pop])
    for ind, fit in zip(pop, fitnesses):
        ind.fitness.values = fit

    for gen in range(GENERATIONS):
        offspring = toolbox.select(pop, len(pop))
        offspring = list(map(toolbox.clone, offspring))

        # Crossover
        for c1, c2 in zip(offspring[::2], offspring[1::2]):
            if random.random() < CROSSOVER_PB:
                toolbox.mate(c1, c2)
                # Clamp genes to [0, 1] after blend crossover (cxBlend can exceed bounds)
                for ind in (c1, c2):
                    for i in range(len(ind)):
                        ind[i] = max(0.0, min(1.0, ind[i]))
                del c1.fitness.values, c2.fitness.values

        # Mutation
        for mutant in offspring:
            if random.random() < MUTATION_PB:
                toolbox.mutate(mutant)
                del mutant.fitness.values

        # Re-evaluate invalid
        invalid = [ind for ind in offspring if not ind.fitness.valid]
        new_fitnesses = await asyncio.gather(*[toolbox.evaluate(ind) for ind in invalid])
        for ind, fit in zip(invalid, new_fitnesses):
            ind.fitness.values = fit

        pop[:] = offspring
        hof.update(pop)
        record = stats.compile(pop)
        logger.debug("[evolution] pod=%s gen=%d max=%.4f avg=%.4f", pod_name, gen, record["max"], record["avg"])

    best = hof[0]
    best_score = best.fitness.values[0]
    result = {
        "pod": pod_name,
        "best_genome": list(best),
        "best_score": best_score,
        "generations": GENERATIONS,
        "population": POPULATION_SIZE,
        "timestamp": time.time(),
    }

    # Persist to Supabase
    if supabase_client:
        try:
            await asyncio.to_thread(
                lambda: supabase_client.table("nexus_evolutions").insert(result).execute()
            )
        except Exception as exc:
            logger.warning("[evolution] supabase persist fail: %s", exc)

    logger.info("[evolution] cycle done pod=%s best_score=%.4f", pod_name, best_score)

    # Publish MAE cycle complete event (S9-01)
    try:
        from events.bus import NexusEvent, publish
        await publish(
            NexusEvent(
                pod=pod_name,
                event_type="nexus.mae_cycle_complete",
                payload={"pod": pod_name, "top_score": best_score, "generation": GENERATIONS},
            ),
            supabase_client=supabase_client,
        )
    except Exception as exc:
        logger.debug("[evolution] event publish fail (non-critical): %s", exc)

    return result


async def run_all_pod_cycles(supabase_client=None) -> list[dict]:
    """Trigger evolution for all registered pods in parallel."""
    tasks = [genetic_cycle(pod, supabase_client) for pod in POD_FITNESS_FNS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]
