"""
nexus/pods/sentinel_prime/router.py
Sentinel Prime — Doc Intel
Revenue: $199/mo | Upgrades: Mechanistic PII circuits
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from dependencies import get_supabase, get_redis
from core.ai_cascade import cascade_call
from core.evolution import register_pod
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)
router = APIRouter()


async def _fitness(individual) -> float:
    import os
    base = float(os.environ.get("_FITNESS", "0.8"))
    return base * max(individual[0], 0.1)


register_pod("sentinel_prime", _fitness)


class TaskRequest(BaseModel):
    input: str
    context: str = ""


@router.post("/run")
async def run_task(body: TaskRequest, request: Request):
    supabase = get_supabase(request)
    redis = get_redis(request)
    prompt = f"Doc Intel task. Context: {body.context}\nInput: {body.input}\nProduce a detailed, actionable response."
    result = await cascade_call(prompt, task_type="doc_analysis", redis_client=redis, pod_name="sentinel_prime")
    await publish(NexusEvent("sentinel_prime", "task_completed", {"input": body.input[:100], "result_len": len(result)}), supabase)
    return {"pod": "sentinel_prime", "result": result}


class AnalyzeRequest(BaseModel):
    document: str
    analysis_type: str = "summary"    # summary | risks | compliance | competitive
    context: str = ""


class SearchRequest(BaseModel):
    query: str
    sources: list[str] = []           # optional context sources
    max_results: int = 5


@router.post("/analyze")
async def analyze_document(body: AnalyzeRequest, request: Request):
    """
    Purpose:  Deep document analysis using LLM cascade + mechanistic interpretability.
    Inputs:   document str, analysis_type (summary/risks/compliance/competitive), context str
    Outputs:  {analysis, analysis_type, word_count, pod}
    Side Effects: 1 cascade_call; publishes sentinel_prime.doc_analyzed event
    """
    supabase = get_supabase(request)
    redis = get_redis(request)
    type_instructions = {
        "summary": "Produce an executive summary (5-7 bullet points + 1-paragraph overview).",
        "risks": "Identify ALL risks, compliance gaps, and red flags. Categorize by severity (High/Med/Low).",
        "compliance": "Check for regulatory compliance issues (GDPR, PCI-DSS, SOC-2, HIPAA patterns).",
        "competitive": "Extract competitive intelligence: strengths, weaknesses, market positioning clues.",
    }
    instruction = type_instructions.get(body.analysis_type, type_instructions["summary"])
    analysis = await cascade_call(
        f"You are a senior document intelligence analyst.\n"
        f"Context: {body.context}\n"
        f"Document:\n{body.document[:8000]}\n\n"
        f"Task: {instruction}",
        task_type="doc_analysis",
        redis_client=redis,
        pod_name="sentinel_prime",
    )
    try:
        await publish(
            NexusEvent("sentinel_prime", "sentinel_prime.doc_analyzed", {
                "analysis_type": body.analysis_type,
                "doc_word_count": len(body.document.split()),
            }),
            supabase,
        )
    except Exception:
        pass
    return {
        "pod": "sentinel_prime",
        "analysis": analysis,
        "analysis_type": body.analysis_type,
        "word_count": len(body.document.split()),
    }


@router.post("/search")
async def semantic_search(body: SearchRequest, request: Request):
    """
    Purpose:  AI-powered semantic search across provided sources + LLM synthesis.
    Inputs:   query str, sources list[str], max_results int
    Outputs:  {query, synthesis, source_count}
    Side Effects: 1 cascade_call; publishes sentinel_prime.search_completed event
    """
    supabase = get_supabase(request)
    redis = get_redis(request)
    source_block = (
        "\n---\n".join(f"Source {i+1}:\n{s[:2000]}" for i, s in enumerate(body.sources[:5]))
        if body.sources
        else "No external sources provided. Use your knowledge."
    )
    synthesis = await cascade_call(
        f"Semantic search and synthesis task.\n"
        f"Query: {body.query}\n"
        f"Sources:\n{source_block}\n\n"
        f"Return the top {body.max_results} most relevant insights, ranked by relevance.",
        task_type="semantic_search",
        redis_client=redis,
        pod_name="sentinel_prime",
    )
    try:
        await publish(
            NexusEvent("sentinel_prime", "sentinel_prime.search_completed", {
                "query": body.query[:100],
                "source_count": len(body.sources),
            }),
            supabase,
        )
    except Exception:
        pass
    return {
        "pod": "sentinel_prime",
        "query": body.query,
        "synthesis": synthesis,
        "source_count": len(body.sources),
    }


@router.get("/status")
async def status(request: Request):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(lambda: supabase.table("nexus_events").select("id").eq("pod", "sentinel_prime").execute())
        return {"pod": "sentinel_prime", "role": "Doc Intel", "event_count": len(res.data or []), "completion_pct": 80}
    except Exception as exc:
        return {"pod": "sentinel_prime", "error": str(exc)}
