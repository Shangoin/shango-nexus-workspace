"""tests/test_brain.py — Sprint 2 S2-04"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_generate_strategic_brief_returns_string():
    """generate_strategic_brief must return a non-empty string."""
    mock_persona = {
        "reconstructed_persona": "Frugal founder",
        "likely_objections": ["Too costly"],
        "proven_openers": ["ROI question"],
        "buying_stage": "consideration",
        "recommended_close": "Thursday 2PM?",
    }

    with patch("pods.aurora.brain.reconstruct_prospect_persona", new_callable=AsyncMock, return_value=mock_persona), \
         patch("pods.aurora.brain.cascade_call", new_callable=AsyncMock, return_value=(
             "1. Buying stage: consideration\n"
             "2. Primary pain: manual lead qualification\n"
             "3. Objection: Too costly — reframe: ROI within 30 days\n"
             "4. Opening: Priya, I saw you're hiring SDRs at TechCorp...\n"
             "5. Close: Thursday at 2 PM?\n"
             "6. Tone: casual\n"
             "7. Red flags: avoid pricing upfront"
         )):
        from pods.aurora.brain import generate_strategic_brief
        brief = await generate_strategic_brief(
            lead={"name": "Priya", "company": "TechCorp", "tier": "high"},
            call_history=[],
        )

    assert isinstance(brief, str)
    assert len(brief) > 100


@pytest.mark.asyncio
async def test_generate_tactical_prompt_returns_string():
    """generate_tactical_prompt must return a deployable prompt string."""
    with patch("pods.aurora.brain.cascade_call", new_callable=AsyncMock, return_value=(
        "You are ARIA, a sales agent specialising in AI automation. "
        "Lead: Priya at TechCorp. Open with the ROI question. "
        "If they object on price, use the 30-day ROI reframe. "
        "Close with: Thursday at 2 PM?"
    )):
        from pods.aurora.brain import generate_tactical_prompt
        prompt = await generate_tactical_prompt(
            strategic_brief="Brief: ROI focus, Thursday close",
            lead={"name": "Priya", "company": "TechCorp"},
        )

    assert isinstance(prompt, str)
    assert len(prompt) > 50


@pytest.mark.asyncio
async def test_generate_strategic_brief_falls_back_on_cascade_error():
    """Should return fallback string on cascade failure, never raise."""
    with patch("pods.aurora.brain.reconstruct_prospect_persona", new_callable=AsyncMock, return_value={}), \
         patch("pods.aurora.brain.cascade_call", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
        from pods.aurora.brain import generate_strategic_brief
        brief = await generate_strategic_brief({"company": "Broken Corp"})

    assert isinstance(brief, str)  # Must never raise
    assert len(brief) > 0
