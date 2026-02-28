"""
nexus/pods/aurora/proactive_scout.py
SIMA 2 self-directed prospect hunting — Aurora finds leads before they find us.

Purpose:  Autonomously scan Serper.dev search results for ICP-matching companies.
          Runs every 6 hours via APScheduler. Feeds discovered prospects directly
          into the Aurora lead ingestion pipeline.
Inputs:   None (autonomous — uses ICP_SIGNALS constants + env keys)
Outputs:  list of prospect dicts ready for lead ingestion
Side Effects: Calls Serper API; publishes aurora.prospects_scouted event;
              POSTs to /api/aurora/leads for each qualified prospect
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ── Ideal Customer Profile signal queries ────────────────────────────────────
ICP_SIGNALS = [
    "B2B SaaS founder Kolkata OR Bangalore OR Mumbai hiring SDR",
    "startup India outbound sales team manual process problem",
    "small business India sales automation CRM pain",
    "India D2C brand founder scaling sales team 2024",
    "B2B startup India looking for sales outsourcing",
    "Indian startup SDR cold calling problem solving",
]

_SERPER_URL = "https://google.serper.dev/search"


async def scout_prospects() -> list[dict]:
    """
    Purpose:  Scan Serper for ICP-matching companies, extract contact signals.
    Inputs:   None (uses ICP_SIGNALS + SERPER_API_KEY env)
    Outputs:  list of prospect dicts with name, company, email (if found)
    Side Effects: Publishes aurora.prospects_scouted; POSTs qualified leads to Aurora
    """
    from core.ai_cascade import cascade_call
    from core.constitution import get_constitution, validate
    from events.bus import NexusEvent, publish

    serper_key = os.getenv("SERPER_API_KEY", "")
    backend_url = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
    const = get_constitution()

    if not serper_key:
        logger.info("[proactive_scout] SERPER_API_KEY not set — skipping scout run")
        return []

    prospects: list[dict] = []

    async with httpx.AsyncClient(timeout=20) as client:
        for signal in ICP_SIGNALS:
            try:
                r = await client.post(
                    _SERPER_URL,
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": signal, "num": 5},
                )
                if r.status_code != 200:
                    logger.warning("[proactive_scout] serper fail signal='%s' status=%d", signal[:40], r.status_code)
                    continue

                results = r.json().get("organic", [])
                for result in results:
                    # Extract company signals from search result
                    try:
                        raw = await cascade_call(
                            f"Extract the following from this search result snippet. "
                            f"Return ONLY valid JSON with keys: "
                            f"name (str or null), company (str or null), "
                            f"email (str or null), pain_point (str), "
                            f"country_code (str, default 'IN'), source (str = 'proactive_scout').\n"
                            f"Search result: {json.dumps(result, default=str)}\n"
                            f"If you cannot extract company name, return null for all fields.",
                            task_type="lead_extraction",
                            pod_name="aurora",
                        )
                        prospect: dict = json.loads(raw.strip())
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.debug("[proactive_scout] extraction fail: %s", exc)
                        continue

                    if not prospect.get("company"):
                        continue

                    # Constitution check — no PII leaks
                    check_text = " ".join(str(v) for v in prospect.values() if v)
                    ok, reason = const.validate(check_text, pod="aurora")
                    if not ok:
                        logger.info("[proactive_scout] constitution block: %s", reason)
                        continue

                    prospect.setdefault("name", "Founder")
                    prospect.setdefault("phone", "")
                    prospect.setdefault("pain_point", "sales automation")
                    prospect.setdefault("country_code", "IN")
                    prospect.setdefault("source", "proactive_scout")
                    prospects.append(prospect)

            except Exception as exc:
                logger.warning("[proactive_scout] signal loop fail signal='%s': %s", signal[:40], exc)

    # Ingest qualified prospects (those with a phone or email signal)
    ingested = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for p in prospects:
            if not p.get("phone") and not p.get("email"):
                continue  # Skip leads with no contact info
            try:
                await client.post(
                    f"{backend_url}/api/aurora/leads",
                    json={
                        "name": p.get("name", "Founder"),
                        "phone": p.get("phone", ""),
                        "country_code": p.get("country_code", "IN"),
                        "company": p.get("company", ""),
                        "pain_point": p.get("pain_point", "sales automation"),
                        "source": "proactive_scout",
                    },
                )
                ingested += 1
            except Exception as exc:
                logger.debug("[proactive_scout] ingest fail: %s", exc)

    try:
        await publish(
            NexusEvent("aurora", "prospects_scouted", {"total_found": len(prospects), "ingested": ingested}),
            supabase_client=None,
        )
    except Exception:
        pass

    logger.info("[proactive_scout] complete — found=%d ingested=%d", len(prospects), ingested)
    return prospects
