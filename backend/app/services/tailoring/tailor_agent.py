"""
Tailor Agent — FR-5.x

Generates tailored resume bullets as (text, [fact_id,...]) tuples.
Only facts from the user's Master Profile Store are injected into the prompt context.
Any bullet produced without fact_ids is dropped before reaching the Grounding Verifier.

Hard constraints enforced:
  - FR-5.1: Output is strictly List[{text, fact_ids[]}]
  - FR-5.2: Bullets with empty fact_ids are rejected and never passed through
  - FR-1.4: This service NEVER calls create_fact_unit() (one-way flow enforced)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import CanonicalJobRecord, TailoredResume
from backend.app.services.llm_client import get_llm_client
from backend.app.services.profile_service import list_facts

log = logging.getLogger("jobflow.tailor")


# ─── Prompt Construction ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a resume tailoring assistant. Your task is to write tailored resume bullet points
for a specific job description using ONLY the provided facts from the candidate's profile.

STRICT RULES:
1. Every bullet point MUST cite at least one fact_id from the provided facts.
2. Do NOT invent, embellish, or extrapolate any information not present in the cited facts.
3. Do NOT combine numbers or metrics from different facts into a single bullet.
4. Output ONLY valid JSON — a list of objects, each with "text" (string) and "fact_ids" (array of strings).
5. Maximum 10 bullets. Each bullet should be 1-2 sentences, action-verb first.
6. Only use facts relevant to the job description. Omit irrelevant facts rather than forcing them in.

Output format (STRICTLY follow this schema, no other text):
[
  {"text": "Action verb + achievement...", "fact_ids": ["FACT-001", "FACT-003"]},
  ...
]"""


def _build_prompt(job: CanonicalJobRecord, facts: list[dict]) -> str:
    # Cap facts payload to top 30 to fit well within LLM context and rate limits
    facts_json = json.dumps(facts[:30], indent=2)
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"JOB DESCRIPTION:\n"
        f"Company: {job.company}\n"
        f"Role: {job.role}\n"
        f"---\n"
        f"{job.jd_text[:2500]}\n"
        f"---\n\n"
        f"CANDIDATE FACTS (use ONLY these):\n"
        f"FACTS_JSON: {facts_json}\n\n"
        f"Generate tailored bullets now (JSON only):"
    )


# ─── Structural Validation ────────────────────────────────────────────────────

def _validate_bullets(raw: object, valid_fact_ids: set[str]) -> list[dict]:
    """
    Validate bullet structure from LLM output.
    - Drops bullets with empty or missing fact_ids (FR-5.2)
    - Drops bullets with fact_ids not in the user's profile (hallucinated IDs)
    - Drops bullets with no text
    Returns only valid bullets.
    """
    if not isinstance(raw, list):
        log.warning(f"Tailor Agent: expected list, got {type(raw).__name__}")
        return []

    valid = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        fact_ids = item.get("fact_ids") or []

        if not text:
            log.warning("Tailor Agent: dropped bullet with empty text")
            continue

        # FR-5.2: Must have at least one fact_id
        if not fact_ids:
            log.warning(f"Tailor Agent: dropped bullet '{text[:60]}...' — no fact_ids")
            continue

        # Only allow fact_ids that exist in the user's profile
        resolved_ids = [fid for fid in fact_ids if fid in valid_fact_ids]
        if not resolved_ids:
            log.warning(
                f"Tailor Agent: dropped bullet '{text[:60]}...' "
                f"— cited fact_ids {fact_ids} not found in profile"
            )
            continue

        valid.append({"text": text, "fact_ids": resolved_ids})

    return valid


# ─── Main Entry Point ─────────────────────────────────────────────────────────

async def tailor_resume(
    db: AsyncSession,
    job: CanonicalJobRecord,
) -> TailoredResume:
    """
    FR-5.x: Generate a tailored TailoredResume for the given job.
    Bullets are returned un-verified at this point; caller must run grounding_verifier.

    Returns a TailoredResume object (not yet persisted — caller commits after verification).
    """
    settings = get_settings()
    client = get_llm_client(provider=settings.llm_provider, model=settings.llm_model)

    # Fetch all facts from the user's profile
    all_facts = await list_facts(db)
    if not all_facts:
        raise ValueError("Master Profile Store is empty. Ingest a resume first.")

    # Serialize facts for prompt injection (deduplicating identical text entries)
    seen_texts = set()
    facts_payload = []
    for f in all_facts:
        norm_text = f.text.strip().lower()
        if norm_text not in seen_texts:
            seen_texts.add(norm_text)
            facts_payload.append({"fact_id": f.fact_id, "type": f.type, "text": f.text})

    valid_fact_ids = {f.fact_id for f in all_facts}

    prompt = _build_prompt(job, facts_payload)

    log.info(f"Tailor Agent: generating bullets for {job.company} / {job.role}")

    try:
        raw_bullets = await client.complete_json(prompt)
    except Exception as exc:
        log.error(f"Tailor Agent: LLM call failed: {exc}")
        raise RuntimeError(f"Tailor Agent generation failed: {exc}") from exc

    validated = _validate_bullets(raw_bullets, valid_fact_ids)
    log.info(
        f"Tailor Agent: {len(raw_bullets) if isinstance(raw_bullets, list) else '?'} raw bullets → "
        f"{len(validated)} passed structural validation"
    )

    if not validated:
        raise RuntimeError(
            "Tailor Agent produced zero valid bullets (all failed structural validation). "
            "Check profile facts and job description."
        )

    # Build the TailoredResume object (grounding_score = 0.0 placeholder until verifier runs)
    resume = TailoredResume(
        resume_id=str(uuid.uuid4()),
        job_fingerprint=job.fingerprint,
        grounding_score=0.0,
        pdf_path=None,
    )
    resume.set_bullets(validated)
    return resume
