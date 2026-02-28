"""tests/test_genome_decoder.py — Sprint 2 S2-01"""
import pytest
from core.genome_decoder import decode_genome, GENE_MAP


def test_decode_genome_aurora_returns_dict():
    params = decode_genome([0.5] * 8, "aurora")
    assert isinstance(params, dict)
    assert "vapi_temperature" in params
    assert 0.3 <= params["vapi_temperature"] <= 1.0


def test_decode_genome_includes_all_base_genes():
    params = decode_genome([0.5] * 8, "nexus")
    for label in GENE_MAP.values():
        assert label in params


def test_decode_genome_aurora_follow_up_days():
    params = decode_genome([0.0, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5], "aurora")
    assert params["follow_up_days"] >= 1


def test_decode_genome_syntropy_persona():
    # gene[5] = 0.8 > 0.5 → drill_sergeant
    params = decode_genome([0.5, 0.5, 0.5, 0.5, 0.5, 0.8, 0.5, 0.5], "syntropy")
    assert params["persona"] == "drill_sergeant"

    # gene[5] = 0.2 ≤ 0.5 → ivy_coach
    params2 = decode_genome([0.5, 0.5, 0.5, 0.5, 0.5, 0.2, 0.5, 0.5], "syntropy")
    assert params2["persona"] == "ivy_coach"


def test_decode_genome_janus_position_size():
    params = decode_genome([0.5] * 8, "janus")
    assert 0.5 <= params["position_size_multiplier"] <= 1.5


def test_decode_genome_clamping():
    # Even with out-of-range values, everything should be clamped
    params = decode_genome([2.0, -1.0, 99.0, 0.5, 0.5, 0.5, 0.5, 0.5], "aurora")
    assert 0.3 <= params["vapi_temperature"] <= 1.0


def test_decode_genome_pads_short_genome():
    # Genome with fewer than 8 genes should not crash
    params = decode_genome([0.5, 0.3], "dan")
    assert isinstance(params, dict)
