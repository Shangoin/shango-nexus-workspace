"""
nexus/pods/syntropy_war_room/router.py
War Room — Exam Arena with MIT SEAL adaptive difficulty.

Purpose:     Expose SEAL adaptive question difficulty to frontend (React/Flutter).
             Students submit answers, receive difficulty-adapted next questions.
Inputs:      StartSessionRequest, AnswerSubmission, student_id path param
Outputs:     QuestionResponse (question, difficulty, question_number, percentile)
Side Effects: Writes performance notes to memory, publishes syntropy.* events
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.ai_cascade import cascade_call
from core.evolution import register_pod
from core.memory import recall, remember
from events.bus import NexusEvent, publish
from pods.syntropy_war_room.seal import inner_loop, outer_loop

logger = logging.getLogger(__name__)
router = APIRouter()


async def _fitness(individual) -> float:
    import os
    base = float(os.environ.get("_FITNESS", "0.85"))
    return base * max(individual[0], 0.1)


register_pod("syntropy_war_room", _fitness)


# ── Pydantic models ───────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    student_id: str
    topic: str          # e.g. "JEE Physics", "NEET Biology", "SAT Math"
    difficulty: float = 0.5   # Initial difficulty 0.0–1.0


class AnswerSubmission(BaseModel):
    student_id: str
    topic: str
    question: str
    student_answer: str
    correct_answer: str
    time_taken_seconds: int = 60
    company: str | None = None         # Sprint 7: cross-sell trigger
    student_email: str = ""            # Sprint 7: cross-sell trigger
    student_name: str = ""             # Sprint 7: cross-sell trigger


class QuestionResponse(BaseModel):
    question: str
    difficulty: float
    question_number: int
    estimated_percentile: float


class TaskRequest(BaseModel):
    input: str
    context: str = ""


# ── SEAL adaptive endpoints ───────────────────────────────────────────────────

@router.post("/session/start", response_model=QuestionResponse)
async def start_session(req: StartSessionRequest):
    """Get first question for a new exam session."""
    result = await inner_loop(req.student_id, req.topic, req.difficulty)
    await publish(
        NexusEvent(
            pod="syntropy",
            event_type="syntropy.session_started",
            payload={"student_id": req.student_id, "topic": req.topic, "difficulty": req.difficulty},
        )
    )
    diff = result.get("difficulty", req.difficulty)
    return QuestionResponse(
        question=result.get("question", ""),
        difficulty=diff,
        question_number=1,
        estimated_percentile=round(diff * 99),
    )


@router.post("/session/answer", response_model=QuestionResponse)
async def submit_answer(submission: AnswerSubmission):
    """Submit answer, score it, return next difficulty-adapted question."""
    # Score the answer via cascade
    scoring_raw = await cascade_call(
        f"Score this answer 0-100.\n"
        f"Question: {submission.question}\n"
        f"Correct answer: {submission.correct_answer}\n"
        f"Student answer: {submission.student_answer}\n"
        f"Return JSON: {{\"score\": int, \"feedback\": str, \"correct\": bool}}",
        task_type="answer_scoring",
        pod_name="syntropy",
    )
    # Handle both dict and string returns from cascade
    if isinstance(scoring_raw, dict):
        scoring = scoring_raw
    else:
        import json
        try:
            scoring = json.loads(str(scoring_raw))
        except Exception:
            correct = submission.student_answer.lower().strip() == submission.correct_answer.lower().strip()
            scoring = {"score": 80 if correct else 20, "correct": correct, "feedback": ""}

    score = scoring.get("score", 0)
    correct = scoring.get("correct", False)

    # Write performance note to memory
    await remember(
        content={
            "topic": submission.topic,
            "score": score,
            "correct": correct,
            "time_seconds": submission.time_taken_seconds,
            "difficulty": 0.5,
        },
        pod="syntropy",
        metadata={"student_id": submission.student_id, "type": "answer_note"},
    )

    # Count questions answered to decide if outer_loop should run
    notes = await recall(
        query=f"student {submission.student_id} performance",
        pod="syntropy",
        top_k=100,
    )
    question_count = len(notes or [])

    # Every 10 questions: full outer_loop recalibration; otherwise micro-adjust
    if question_count % 10 == 0 and question_count > 0:
        new_difficulty = await outer_loop(submission.student_id)
        # Sprint 7: Syntropy → Aurora cross-sell trigger (ERS ≥ 75 + company known)
        if new_difficulty >= 0.75 and submission.company:
            n8n_url = os.environ.get("N8N_URL", "")
            if n8n_url:
                try:
                    async with httpx.AsyncClient(timeout=5) as _client:
                        await _client.post(
                            f"{n8n_url}/webhook/syntropy-ers-milestone",
                            json={
                                "student_id": submission.student_id,
                                "student_email": submission.student_email,
                                "student_name": submission.student_name or submission.student_id,
                                "topic": submission.topic,
                                "ers_score": round(new_difficulty * 100),
                                "percentile": round(new_difficulty * 99),
                                "company": submission.company,
                            },
                        )
                    logger.info("[war_room] ERS cross-sell triggered for %s", submission.student_id)
                except Exception as _exc:
                    logger.warning("[war_room] ERS cross-sell trigger failed: %s", _exc)
    else:
        current = notes[-1].get("difficulty", 0.5) if notes else 0.5
        new_difficulty = min(1.0, max(0.0, current + (0.05 if correct else -0.05)))

    # Generate next adapted question
    next_q = await inner_loop(submission.student_id, submission.topic, new_difficulty)

    await publish(
        NexusEvent(
            pod="syntropy",
            event_type="syntropy.answer_submitted",
            payload={
                "student_id": submission.student_id,
                "score": score,
                "correct": correct,
                "difficulty": new_difficulty,
            },
        )
    )

    return QuestionResponse(
        question=next_q.get("question", ""),
        difficulty=new_difficulty,
        question_number=question_count + 1,
        estimated_percentile=round(new_difficulty * 99),
    )


@router.get("/session/performance/{student_id}")
async def get_performance(student_id: str):
    """Get student's full performance history and current difficulty level."""
    notes = await recall(
        query=f"student {student_id} performance",
        pod="syntropy",
        top_k=50,
    )
    total = len(notes or [])
    correct_count = sum(1 for n in (notes or []) if isinstance(n, dict) and n.get("correct"))
    current_diff = notes[-1].get("difficulty", 0.5) if notes else 0.5
    return {
        "student_id": student_id,
        "total_questions": total,
        "correct": correct_count,
        "accuracy": round(correct_count / total * 100, 1) if total else 0.0,
        "current_difficulty": current_diff,
        "estimated_percentile": round(current_diff * 99),
    }


# ── Legacy generic run endpoint ───────────────────────────────────────────────

@router.post("/run")
async def run_task(body: TaskRequest, request: Request):
    from dependencies import get_redis, get_supabase
    supabase = get_supabase(request)
    redis = get_redis(request)
    prompt = (
        f"Exam Arena task. Context: {body.context}\nInput: {body.input}\n"
        f"Produce a detailed, actionable response."
    )
    result = await cascade_call(
        prompt, task_type="exam_prep", redis_client=redis, pod_name="syntropy_war_room"
    )
    await publish(
        NexusEvent(
            pod="syntropy_war_room",
            event_type="task_completed",
            payload={"input": body.input[:100], "result_len": len(result)},
        ),
        supabase,
    )
    return {"pod": "syntropy_war_room", "result": result}


@router.get("/status")
async def status(request: Request):
    from dependencies import get_supabase
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(
            lambda: supabase.table("nexus_events")
            .select("id")
            .eq("pod", "syntropy_war_room")
            .execute()
        )
        return {
            "pod": "syntropy_war_room",
            "role": "Exam Arena + SEAL Adaptive Difficulty",
            "event_count": len(res.data or []),
            "completion_pct": 90,
            "seal_endpoints": ["POST /session/start", "POST /session/answer", "GET /session/performance/{id}"],
        }
    except Exception as exc:
        return {"pod": "syntropy_war_room", "error": str(exc)}
