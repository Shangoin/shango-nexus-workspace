"""tests/test_rl_variants.py — Sprint 3 S3-01"""
import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.asyncio
async def test_generate_variants_returns_list_of_five():
    """generate_variants must return a list of exactly 5 strings."""
    mock_response = json.dumps([
        "Hi {name}, quick question?",
        "Hey {name}, worth 2 minutes?",
        "Hi {name}, what's your biggest outreach challenge?",
        "{name}, saw your hire post — still manual?",
        "Hi {name}, how long does follow-up take?",
    ])

    with patch("pods.aurora.rl_variants.cascade_call", new_callable=AsyncMock, return_value=mock_response):
        from pods.aurora.rl_variants import generate_variants
        variants = await generate_variants("opener", {"company": "Acme", "tier": "high"}, n=5)

    assert isinstance(variants, list)
    assert len(variants) == 5
    assert all(isinstance(v, str) for v in variants)


@pytest.mark.asyncio
async def test_generate_variants_fallback_on_json_error():
    """Should return default fallback list when cascade returns invalid JSON."""
    with patch("pods.aurora.rl_variants.cascade_call", new_callable=AsyncMock, return_value="not json"):
        from pods.aurora.rl_variants import generate_variants
        variants = await generate_variants("opener", {}, n=5)

    assert isinstance(variants, list)
    assert len(variants) >= 1


@pytest.mark.asyncio
async def test_select_variant_returns_valid_tuple():
    """select_variant must return (str, int) with valid index."""
    variants = ["opener A", "opener B", "opener C"]

    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=[]):
        from pods.aurora.rl_variants import select_variant
        selected, idx = await select_variant("opener", variants)

    assert isinstance(selected, str)
    assert isinstance(idx, int)
    assert 0 <= idx < len(variants)
    assert selected in variants


@pytest.mark.asyncio
async def test_record_outcome_does_not_raise():
    """record_outcome must never raise even on memory failure."""
    with patch("pods.aurora.rl_variants.remember", new_callable=AsyncMock, side_effect=RuntimeError("memory down")):
        from pods.aurora.rl_variants import record_outcome
        # Should log warning and return None, not raise
        await record_outcome("opener", 0, "test variant", meeting_booked=True)


def test_variant_elements_defined():
    from pods.aurora.rl_variants import VARIANT_ELEMENTS
    assert "opener" in VARIANT_ELEMENTS
    assert "closing_ask" in VARIANT_ELEMENTS
    assert len(VARIANT_ELEMENTS) == 4
