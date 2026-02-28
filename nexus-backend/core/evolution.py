"""
nexus/core/evolution.py
MARS genetic evolution engine (DEAP-based).
Each pod exposes a fitness function via POD_FITNESS_FNS registry.
Every CYCLE_THRESHOLD events, genetic_cycle() runs for all active pods.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
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

    toolbox = base.Toolbox()
    toolbox.register("individual", _make_individual)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("evaluate", _evaluate, fitness_fn=fitness_fn)
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
    return result


async def run_all_pod_cycles(supabase_client=None) -> list[dict]:
    """Trigger evolution for all registered pods in parallel."""
    tasks = [genetic_cycle(pod, supabase_client) for pod in POD_FITNESS_FNS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]
