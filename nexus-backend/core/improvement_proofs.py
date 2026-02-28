"""
nexus/core/improvement_proofs.py
Cryptographically signed immutable proofs for every MARS/DEAP improvement cycle.

Purpose:  AlphaProof-style formal verification. Every improvement cycle produces an
          auditable proof record showing whether performance improved, signed with
          SHA-256 (existing) and RSA-2048 (Sprint 6) for enterprise non-repudiation.
          Stored in 3-tier memory as immutable audit trail.
Inputs:   pod_name, cycle_id, avg_score_before/after, genome list, n_calls
Outputs:  proof_data dict with all fields + proof_hash (SHA-256, 64 chars)
          + rsa_signature (base64 RSA-2048, Sprint 6)
Side Effects: Writes proof to memory layer (L2 pgvector + L3 mem0)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Module-level import for testability
try:
    from core.memory import remember
except ImportError:  # pragma: no cover
    remember = None  # type: ignore[assignment]


# ── Sprint 6: RSA-2048 signing ────────────────────────────────────────────────

# Process-level key cache — ensures sign + verify use the same keypair in tests
_rsa_private_key_cache = None


def get_private_key():
    """
    Purpose:     Load RSA private key from NEXUS_RSA_PRIVATE_KEY env var,
                 or generate a fresh keypair on first run (printing to stdout).
    Inputs:      None (reads env)
    Outputs:     RSA private key object
    Side Effects: Prints PEM on first run (one-time only) so operator can save to .env
    """
    global _rsa_private_key_cache
    if _rsa_private_key_cache is not None:
        return _rsa_private_key_cache

    import os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    pem = os.getenv("NEXUS_RSA_PRIVATE_KEY", "").replace("\\n", "\n")
    if pem:
        try:
            _rsa_private_key_cache = serialization.load_pem_private_key(
                pem.encode(), password=None, backend=default_backend()
            )
            return _rsa_private_key_cache
        except Exception as exc:
            logger.warning("[improvement_proofs] failed to load RSA key from env: %s — generating new", exc)

    # Generate new keypair (first run)
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Inline the PEM so it can be copied as a single line
    inline = pem_bytes.decode().replace("\n", "\\n")
    print(f"\n[improvement_proofs] ADD TO .env:\nNEXUS_RSA_PRIVATE_KEY={inline}\n")
    logger.info("[improvement_proofs] generated new RSA-2048 keypair — copy from stdout to .env")
    _rsa_private_key_cache = private_key
    return private_key


def sign_proof_rsa(proof_data: dict) -> str:
    """
    Purpose:     RSA-2048 sign a proof dict for enterprise non-repudiation.
    Inputs:      proof_data dict (must be JSON-serialisable, must NOT include rsa_signature key)
    Outputs:     base64-encoded RSA-PKCS1v15/SHA-256 signature string
    Side Effects: None
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = get_private_key()
    message = json.dumps(proof_data, sort_keys=True, separators=(",", ":")).encode()
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def verify_proof_rsa(proof_data: dict, signature_b64: str) -> bool:
    """
    Purpose:     Verify RSA-2048 signature on a proof record.
    Inputs:      proof_data dict (without rsa_signature key), base64 signature
    Outputs:     True if signature valid; False if tampered or key mismatch
    Side Effects: None
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = get_private_key()
        public_key = private_key.public_key()
        message = json.dumps(proof_data, sort_keys=True, separators=(",", ":")).encode()
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


async def generate_improvement_proof(
    pod_name: str,
    cycle_id: str,
    avg_score_before: float,
    avg_score_after: float,
    genome: list[float],
    n_calls: int,
) -> dict[str, Any]:
    """
    Purpose:  Generate and store a signed proof of improvement for a DEAP/MARS cycle.
              Sprint 6: Also includes RSA-2048 signature for non-repudiation.
    Inputs:   pod_name str; cycle_id str (UUID); avg_score_before float;
              avg_score_after float; genome list[float]; n_calls int
    Outputs:  proof_data dict with improved bool, proof_hash (SHA-256), rsa_signature (Sprint 6)
    Side Effects: Writes proof to memory layer (fails gracefully if unavailable)
    """
    improved = avg_score_after > avg_score_before
    delta = round(avg_score_after - avg_score_before, 6)

    proof_data: dict[str, Any] = {
        "pod": pod_name,
        "cycle_id": cycle_id,
        "timestamp": round(time.time(), 3),
        "avg_score_before": round(avg_score_before, 6),
        "avg_score_after": round(avg_score_after, 6),
        "delta": delta,
        "improved": improved,
        "genome_hash": hashlib.sha256(json.dumps(genome, separators=(",", ":")).encode()).hexdigest(),
        "n_calls": n_calls,
        "version": "2.0",
    }

    # SHA-256 signing (existing — deterministic on sorted keys)
    proof_payload = json.dumps(proof_data, sort_keys=True, separators=(",", ":"))
    proof_hash = hashlib.sha256(proof_payload.encode()).hexdigest()
    proof_data["proof_hash"] = proof_hash

    # RSA-2048 signing (Sprint 6 — non-repudiation upgrade)
    try:
        rsa_sig = sign_proof_rsa({k: v for k, v in proof_data.items() if k != "rsa_signature"})
        proof_data["rsa_signature"] = rsa_sig
    except Exception as exc:
        logger.warning("[improvement_proofs] RSA signing failed (proof still stored): %s", exc)
        proof_data["rsa_signature"] = None

    # Persist as immutable record
    try:
        await remember(
            content=proof_data,
            pod=pod_name,
            metadata={
                "type": "improvement_proof",
                "improved": str(improved),
                "cycle_id": cycle_id,
            },
        )
        logger.info(
            "[improvement_proofs] stored pod=%s cycle=%s improved=%s delta=%.4f hash=%s...",
            pod_name, cycle_id, improved, delta, proof_hash[:12],
        )
    except Exception as exc:
        logger.warning("[improvement_proofs] remember fail (proof is still returned): %s", exc)

    return proof_data


def verify_proof(proof_data: dict[str, Any]) -> bool:
    """
    Purpose:  Verify a stored proof's integrity by recomputing its hash.
    Inputs:   proof_data dict (must include proof_hash key)
    Outputs:  True if hash matches (proof unmodified), False otherwise
    Side Effects: None
    """
    stored_hash = proof_data.get("proof_hash", "")
    # Re-compute without the proof_hash field itself
    payload_dict = {k: v for k, v in proof_data.items() if k != "proof_hash"}
    recomputed = hashlib.sha256(
        json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    valid = recomputed == stored_hash
    if not valid:
        logger.error("[improvement_proofs] TAMPERED proof detected — hash mismatch")
    return valid


async def get_cycle_history(pod_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Purpose:  Retrieve the last N improvement proofs for a pod.
    Inputs:   pod_name str; limit int
    Outputs:  list of proof dicts sorted by timestamp descending
    Side Effects: None (read-only memory query)
    """
    from core.memory import recall

    try:
        results = await recall(
            query=f"{pod_name} improvement_proof cycle",
            pod=pod_name,
            top_k=limit,
        )
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict) and "proof_hash" in r]
        return []
    except Exception as exc:
        logger.warning("[improvement_proofs] get_cycle_history fail: %s", exc)
        return []
