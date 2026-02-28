"""tests/test_reconstructive_memory.py — Sprint 2 S2-03"""
import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.asyncio
async def test_reconstruct_returns_required_keys():
    """reconstruct_prospect_persona must return dict with all required keys."""
    mock_recall_data = [{"transcript": "They care about ROI", "outcome": "booked"}]
    mock_cascade_response = json.dumps({
        "reconstructed_persona": "Cost-conscious SaaS founder",
        "likely_objections": ["Too expensive", "No time", "Not a priority"],
        "proven_openers": ["ROI question", "Pain-first opener"],
        "buying_stage": "consideration",
        "recommended_close": "Tuesday 3PM?",
    })

    with patch("pods.aurora.reconstructive_memory.recall", new_callable=AsyncMock, return_value=mock_recall_data), \
         patch("pods.aurora.reconstructive_memory.cascade_call", new_callable=AsyncMock, return_value=mock_cascade_response), \
         patch("pods.aurora.reconstructive_memory.remember", new_callable=AsyncMock):
        from pods.aurora.reconstructive_memory import reconstruct_prospect_persona
        result = await reconstruct_prospect_persona({"company": "Acme", "pain_point": "manual outreach"})

    assert "likely_objections" in result
    assert "reconstructed_persona" in result
    assert "buying_stage" in result
    assert "recommended_close" in result
    assert isinstance(result["likely_objections"], list)


@pytest.mark.asyncio
async def test_reconstruct_returns_empty_result_when_no_recall():
    """Should return default empty result gracefully when no memory found."""
    with patch("pods.aurora.reconstructive_memory.recall", new_callable=AsyncMock, return_value=[]):
        from pods.aurora.reconstructive_memory import reconstruct_prospect_persona
        result = await reconstruct_prospect_persona({"company": "NoData Inc"})

    assert result["reconstructed_persona"] == "No prior data — treat as cold prospect"
    assert result["likely_objections"] == []


@pytest.mark.asyncio
async def test_reconstruct_handles_json_parse_error():
    """Should return empty default when cascade returns malformed JSON."""
    with patch("pods.aurora.reconstructive_memory.recall", new_callable=AsyncMock, return_value=[{"data": "x"}]), \
         patch("pods.aurora.reconstructive_memory.cascade_call", new_callable=AsyncMock, return_value="not json"):
        from pods.aurora.reconstructive_memory import reconstruct_prospect_persona
        result = await reconstruct_prospect_persona({"company": "Test"})

    assert "likely_objections" in result  # Returns empty default
