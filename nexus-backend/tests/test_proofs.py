"""tests/test_proofs.py — Sprint 4 S4-02"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_generate_proof_when_improved():
    """improved=True when avg_score_after > avg_score_before."""
    with patch("core.improvement_proofs.remember", new_callable=AsyncMock):
        from core.improvement_proofs import generate_improvement_proof
        proof = await generate_improvement_proof(
            pod_name="aurora",
            cycle_id="cycle-001",
            avg_score_before=0.60,
            avg_score_after=0.72,
            genome=[0.5] * 8,
            n_calls=25,
        )

    assert proof["improved"] is True
    assert proof["delta"] > 0
    assert len(proof["proof_hash"]) == 64  # SHA-256 hex = 64 chars


@pytest.mark.asyncio
async def test_generate_proof_when_not_improved():
    """improved=False when avg_score_after <= avg_score_before."""
    with patch("core.improvement_proofs.remember", new_callable=AsyncMock):
        from core.improvement_proofs import generate_improvement_proof
        proof = await generate_improvement_proof(
            pod_name="janus",
            cycle_id="cycle-002",
            avg_score_before=0.75,
            avg_score_after=0.68,
            genome=[0.3] * 8,
            n_calls=25,
        )

    assert proof["improved"] is False
    assert proof["delta"] < 0
    assert len(proof["proof_hash"]) == 64


def test_verify_proof_valid():
    """verify_proof returns True for an unmodified proof."""
    from core.improvement_proofs import verify_proof
    import hashlib, json, time

    data = {
        "pod": "aurora",
        "cycle_id": "test-cycle",
        "timestamp": 1700000000.0,
        "avg_score_before": 0.6,
        "avg_score_after": 0.7,
        "delta": 0.1,
        "improved": True,
        "genome_hash": "abc123",
        "n_calls": 25,
        "version": "1.0",
    }
    # Compute and attach proof hash as the real function does
    data["proof_hash"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert verify_proof(data) is True


def test_verify_proof_tampered():
    """verify_proof returns False when proof data is tampered."""
    from core.improvement_proofs import verify_proof
    import hashlib, json

    data = {
        "pod": "aurora",
        "cycle_id": "test-cycle",
        "timestamp": 1700000000.0,
        "avg_score_before": 0.6,
        "avg_score_after": 0.7,
        "delta": 0.1,
        "improved": True,
        "genome_hash": "abc123",
        "n_calls": 25,
        "version": "1.0",
    }
    data["proof_hash"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    # Tamper with the data after signing
    data["avg_score_after"] = 0.99
    assert verify_proof(data) is False


@pytest.mark.asyncio
async def test_generate_proof_genome_hash_is_deterministic():
    """Same genome always produces same genome_hash."""
    with patch("core.improvement_proofs.remember", new_callable=AsyncMock):
        from core.improvement_proofs import generate_improvement_proof
        genome = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        proof1 = await generate_improvement_proof("aurora", "c1", 0.5, 0.6, genome, 25)
        proof2 = await generate_improvement_proof("aurora", "c2", 0.5, 0.6, genome, 25)

    assert proof1["genome_hash"] == proof2["genome_hash"]
