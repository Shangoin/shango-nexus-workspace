"""
tests/test_sprint8.py
Sprint 8 smoke tests — Multi-region deploy validation.
Run: pytest tests/test_sprint8.py -v
Target: 8 tests → total 73/73 across all sprints
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent  # shango-nexus-workspace/


# ── S8-01: render.yaml validation ────────────────────────────────────────────

def test_render_yaml_exists():
    """render.yaml must be present at workspace root."""
    assert (ROOT / "render.yaml").exists(), "render.yaml missing from root"


def test_render_yaml_has_two_backend_regions():
    """render.yaml must declare both SG and US backend services."""
    content = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "nexus-backend-sg" in content, "Singapore backend service missing"
    assert "nexus-backend-us" in content, "US backend service missing"
    assert "singapore" in content, "'singapore' region missing"
    assert "oregon" in content, "'oregon' region missing"


def test_render_yaml_has_env_group():
    """render.yaml must use a nexus-secrets env group with key secrets sync:false."""
    content = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "nexus-secrets" in content, "nexus-secrets env group missing"
    assert "SUPABASE_URL" in content, "SUPABASE_URL missing from env group"
    assert "GEMINI_API_KEY" in content, "GEMINI_API_KEY missing from env group"
    assert "sync: false" in content, "Secrets must use sync:false (not hardcoded)"


# ── S8-02: vercel.json validation ─────────────────────────────────────────────

def test_vercel_json_exists():
    """landing/vercel.json must exist."""
    vercel_path = ROOT / "landing" / "vercel.json"
    assert vercel_path.exists(), "landing/vercel.json missing"


def test_vercel_json_has_security_headers():
    """vercel.json must include X-Frame-Options, CSP, and X-Content-Type-Options."""
    vercel_path = ROOT / "landing" / "vercel.json"
    config = json.loads(vercel_path.read_text(encoding="utf-8"))
    header_blocks = config.get("headers", [])
    all_header_keys = [
        h["key"]
        for block in header_blocks
        for h in block.get("headers", [])
    ]
    assert "X-Frame-Options" in all_header_keys, "X-Frame-Options header missing"
    assert "Content-Security-Policy" in all_header_keys, "CSP header missing"
    assert "X-Content-Type-Options" in all_header_keys, "X-Content-Type-Options header missing"


# ── S8-03: CI workflow validation ─────────────────────────────────────────────

def test_github_actions_workflow_exists():
    """GitHub Actions CI workflow must exist at .github/workflows/nexus-ci.yml."""
    workflow = ROOT / ".github" / "workflows" / "nexus-ci.yml"
    assert workflow.exists(), ".github/workflows/nexus-ci.yml missing"


# ── S8-02: Region-aware API selector ──────────────────────────────────────────

def test_api_lib_exists():
    """landing/src/lib/api.ts must exist."""
    api_lib = ROOT / "landing" / "src" / "lib" / "api.ts"
    assert api_lib.exists(), "landing/src/lib/api.ts missing"


def test_api_lib_has_region_logic():
    """api.ts must export API_BASE with SG/US region-aware selection."""
    api_lib = ROOT / "landing" / "src" / "lib" / "api.ts"
    content = api_lib.read_text(encoding="utf-8")
    assert "API_BASE" in content, "API_BASE export missing"
    assert "nexus-backend-sg" in content, "Singapore URL missing"
    assert "nexus-backend-us" in content, "US URL missing"
