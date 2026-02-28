"""tests/test_seal.py — Sprint 3 S3-02"""
import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.asyncio
async def test_inner_loop_returns_question():
    """inner_loop must return dict with 'question' key."""
    mock_q = json.dumps({
        "question": "What is the derivative of x^2?",
        "options": ["2x", "x^2", "x", "2"],
        "correct": "A",
        "explanation": "Power rule: d/dx(x^n) = nx^(n-1)",
    })

    with patch("pods.syntropy_war_room.seal.cascade_call", new_callable=AsyncMock, return_value=mock_q):
        from pods.syntropy_war_room.seal import inner_loop
        result = await inner_loop("student_001", "calculus", 0.5)

    assert "question" in result
    assert "difficulty" in result
    assert result["difficulty"] == 0.5


@pytest.mark.asyncio
async def test_inner_loop_scores_correct_answer():
    """inner_loop with correct answer should return score=1.0."""
    mock_q = json.dumps({
        "question": "What is 2+2?",
        "options": ["3", "4", "5", "6"],
        "correct": "B",
        "explanation": "Simple addition",
    })

    with patch("pods.syntropy_war_room.seal.cascade_call", new_callable=AsyncMock, return_value=mock_q), \
         patch("pods.syntropy_war_room.seal.remember", new_callable=AsyncMock):
        from pods.syntropy_war_room.seal import inner_loop
        result = await inner_loop("student_001", "arithmetic", 0.2, student_answer="B")

    assert result["score"] == 1.0
    assert result["is_correct"] is True


@pytest.mark.asyncio
async def test_inner_loop_scores_wrong_answer():
    """inner_loop with wrong answer should return score=0.0."""
    mock_q = json.dumps({
        "question": "What is 2+2?",
        "options": ["3", "4", "5", "6"],
        "correct": "B",
        "explanation": "Simple addition",
    })

    with patch("pods.syntropy_war_room.seal.cascade_call", new_callable=AsyncMock, return_value=mock_q), \
         patch("pods.syntropy_war_room.seal.remember", new_callable=AsyncMock):
        from pods.syntropy_war_room.seal import inner_loop
        result = await inner_loop("student_001", "arithmetic", 0.2, student_answer="A")

    assert result["score"] == 0.0
    assert result["is_correct"] is False


@pytest.mark.asyncio
async def test_outer_loop_returns_float_in_range():
    """outer_loop must return a float strictly in [0.0, 1.0]."""
    mock_notes = [{"is_correct": True, "score": 0.9, "difficulty": 0.4}] * 10

    with patch("pods.syntropy_war_room.seal.recall", new_callable=AsyncMock, return_value=mock_notes), \
         patch("pods.syntropy_war_room.seal.cascade_call", new_callable=AsyncMock, return_value="0.72"), \
         patch("pods.syntropy_war_room.seal.remember", new_callable=AsyncMock):
        from pods.syntropy_war_room.seal import outer_loop
        difficulty = await outer_loop("student_001")

    assert isinstance(difficulty, float)
    assert 0.0 <= difficulty <= 1.0


@pytest.mark.asyncio
async def test_outer_loop_returns_default_when_no_notes():
    """outer_loop returns 0.5 when no notes exist."""
    with patch("pods.syntropy_war_room.seal.recall", new_callable=AsyncMock, return_value=[]):
        from pods.syntropy_war_room.seal import outer_loop
        difficulty = await outer_loop("new_student")

    assert difficulty == 0.5
