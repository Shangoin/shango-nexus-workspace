"""
nexus/pods/syntropy_war_room/seal.py
MIT SEAL (Self-Edits with Notes) for adaptive exam question difficulty.

Purpose:  Inner loop generates questions at current difficulty and awaits answer.
          Outer loop batches 10 performance notes and recalibrates difficulty.
          Implements SEAL paper's write-note → rewrite-model pattern for JEE/NEET/SAT.
Inputs:   student_id str, topic str, current_difficulty float [0.0–1.0],
          student_answer str (for scoring)
Outputs:  Inner loop → {question, difficulty, note_id}; Outer loop → float (new difficulty)
Side Effects: Writes learning notes and difficulty calibration to memory layer
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def inner_loop(
    student_id: str,
    topic: str,
    current_difficulty: float,
    student_answer: str | None = None,
) -> dict:
    """
    Purpose:  Generate question at current_difficulty; evaluate answer if provided.
    Inputs:   student_id, topic, current_difficulty [0.0–1.0], optional student_answer
    Outputs:  dict with 'question', 'difficulty', optionally 'score' and 'note'
    Side Effects: Writes performance note to memory when answer provided
    """
    from core.ai_cascade import cascade_call
    from core.memory import remember

    difficulty_label = (
        "easy (conceptual understanding)"
        if current_difficulty < 0.33
        else ("medium (application, 2-step problems)" if current_difficulty < 0.66
              else "hard (advanced reasoning, multi-concept integration)")
    )
    exam_type = "JEE/NEET" if current_difficulty > 0.5 else "SAT/JEE Mains"

    try:
        question = await cascade_call(
            f"Generate ONE {topic} exam question for {exam_type} at {difficulty_label} level.\n"
            f"Difficulty score: {current_difficulty:.2f}/1.0\n"
            f"Include: question text, 4 multiple-choice options (A/B/C/D), correct answer, brief explanation.\n"
            f"Return ONLY valid JSON: {{\"question\": str, \"options\": [str,str,str,str], "
            f"\"correct\": str, \"explanation\": str}}",
            task_type="question_gen",
            pod_name="syntropy",
        )

        q_data: dict = {}
        try:
            q_data = json.loads(question.strip())
        except json.JSONDecodeError:
            q_data = {"question": question, "options": [], "correct": "", "explanation": ""}

        result = {"question": q_data.get("question", question), "difficulty": current_difficulty, "q_data": q_data}

        # Evaluate answer if provided
        if student_answer is not None:
            correct_answer = q_data.get("correct", "")
            is_correct = student_answer.strip().upper() == correct_answer.strip().upper()
            score = 1.0 if is_correct else 0.0

            note = {
                "student_id": student_id,
                "topic": topic,
                "difficulty": current_difficulty,
                "is_correct": is_correct,
                "score": score,
                "student_answer": student_answer,
                "correct_answer": correct_answer,
            }

            try:
                await remember(
                    content=note,
                    pod="syntropy",
                    metadata={"type": "seal_note", "student_id": student_id, "topic": topic},
                )
            except Exception as exc:
                logger.warning("[seal] remember fail: %s", exc)

            result["score"] = score
            result["is_correct"] = is_correct
            result["note"] = note

        return result

    except Exception as exc:
        logger.error("[seal] inner_loop fail: %s", exc)
        return {"question": f"[Error generating question: {exc}]", "difficulty": current_difficulty}


async def outer_loop(student_id: str) -> float:
    """
    Purpose:  Review 10 recent performance notes → compute new difficulty target.
    Inputs:   student_id str
    Outputs:  float in [0.0, 1.0] — new recommended difficulty
    Side Effects: Writes updated difficulty calibration to memory
    """
    from core.memory import recall, remember
    from core.ai_cascade import cascade_call

    default_difficulty = 0.5

    try:
        notes = await recall(
            query=f"student_{student_id}_performance seal_note",
            pod="syntropy",
            top_k=10,
        )

        if not notes:
            logger.info("[seal] outer_loop: no notes found for student=%s, returning 0.5", student_id)
            return default_difficulty

        raw = await cascade_call(
            f"Analyze these 10 student performance notes and recommend the optimal difficulty (0.0–1.0).\n"
            f"Notes: {notes}\n"
            f"Rules: if avg score >0.8 → increase difficulty; if <0.4 → decrease; else maintain.\n"
            f"Return ONLY a float between 0.0 and 1.0. No explanation.",
            task_type="difficulty_calibration",
            pod_name="syntropy",
        )

        new_difficulty = float(raw.strip().split()[0])
        new_difficulty = max(0.0, min(1.0, new_difficulty))

        try:
            await remember(
                content={"difficulty": new_difficulty, "student_id": student_id, "updated": True},
                pod="syntropy",
                metadata={"type": "difficulty_calibration", "student_id": student_id},
            )
        except Exception:
            pass

        logger.info("[seal] outer_loop student=%s new_difficulty=%.2f", student_id, new_difficulty)
        return new_difficulty

    except (ValueError, Exception) as exc:
        logger.warning("[seal] outer_loop fail: %s — returning default", exc)
        return default_difficulty
