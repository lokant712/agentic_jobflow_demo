"""
Master Profile Service — FR-1.x

Handles:
  - Ingesting raw resume text into atomic FactUnit records (FACT-001, FACT-002, ...)
  - CRUD for FactUnits (user is source of truth; no auto-generation from tailored output)
  - resolve_facts(fact_ids) helper used by the Grounding Verifier

One-way constraint enforced at service level:
  create_fact_unit() is the ONLY entry point for new facts.
  The Tailor Agent NEVER calls this — it only reads via resolve_facts().
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import FactUnit, FACT_TYPE_VALUES


# ─── Fact ID generation ────────────────────────────────────────────────────────

async def _next_fact_id(db: AsyncSession) -> str:
    """Generate the next sequential FACT-NNN ID."""
    result = await db.execute(select(func.count()).select_from(FactUnit))
    count = result.scalar_one()
    return f"FACT-{(count + 1):03d}"


# ─── Ingestion ─────────────────────────────────────────────────────────────────

_SENTENCE_RE = re.compile(r"(?<=[.!?•\-\n])\s+")

# Heuristic type classifier for ingested sentences
_TYPE_HINTS = {
    "tool":           r"\b(python|java|sql|aws|gcp|azure|react|node|docker|kubernetes|"
                      r"tensorflow|pytorch|spark|kafka|redis|postgres|mongodb|git|"
                      r"jira|figma|excel|tableau|power\s?bi|salesforce)\b",
    "metric":         r"\d+[\.,]?\d*\s*(%|x|\$|\u20ac|£|k|m|b|ms|s\b|users|customers|"
                      r"transactions|requests|queries|points|nps|arpu|mrr|arr|\bpercent\b|"
                      r"\bbillion\b|\bmillion\b|\bthousand\b)",
    "outcome":        r"\b(result|impact|achieve|deliver|launch|ship|increase|decrease|"
                      r"reduce|improve|save|generate|win|earn|raise|secure|lead|drive|"
                      r"enable|accelerate|streamline)\w*\b",
    "responsibility": r".*",  # fallback
}


def _classify_type(text: str) -> str:
    text_lower = text.lower()
    for fact_type, pattern in _TYPE_HINTS.items():
        if fact_type == "responsibility":
            continue
        if re.search(pattern, text_lower):
            return fact_type
    return "responsibility"


def _segment_resume(raw_text: str) -> list[str]:
    """
    Split raw resume text into atomic clause-level segments.
    Handles common resume bullet formats (•, -, *, newline-separated).
    """
    # Normalize line endings
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    # Split on common bullet markers and sentence endings
    segments = re.split(r"\n|•|‣|◦|·|(?<=[a-z0-9])\.\s+(?=[A-Z])", text)

    cleaned: list[str] = []
    for seg in segments:
        seg = seg.strip().lstrip("-*•·◦‣ \t")
        # Discard section headers, empty lines, and very short fragments
        if len(seg) < 20:
            continue
        # Discard lines that look like headers (ALL CAPS, no verb-like content)
        if seg.isupper() and len(seg.split()) <= 5:
            continue
        cleaned.append(seg)

    return cleaned


async def ingest_resume(
    db: AsyncSession,
    raw_text: str,
    source_document: str = "resume",
) -> list[FactUnit]:
    """
    Decompose raw resume text into atomic FactUnit records.
    FR-1.1, FR-1.2: One claim per unit with unique immutable fact_id.
    FR-1.4: This is the ONLY place FactUnits are created during ingestion.

    Returns the list of newly created FactUnit objects.
    """
    segments = _segment_resume(raw_text)
    created: list[FactUnit] = []

    for seg in segments:
        fact_id = await _next_fact_id(db)
        fact_type = _classify_type(seg)
        unit = FactUnit(
            fact_id=fact_id,
            type=fact_type,
            text=seg,
            source_document=source_document,
        )
        db.add(unit)
        created.append(unit)
        # Flush after each to get monotonic IDs (next_fact_id reads count)
        await db.flush()

    await db.commit()
    return created


# ─── CRUD ─────────────────────────────────────────────────────────────────────

async def list_facts(db: AsyncSession) -> list[FactUnit]:
    result = await db.execute(select(FactUnit).order_by(FactUnit.fact_id))
    return list(result.scalars().all())


async def get_fact(db: AsyncSession, fact_id: str) -> FactUnit | None:
    result = await db.execute(select(FactUnit).where(FactUnit.fact_id == fact_id))
    return result.scalar_one_or_none()


async def create_fact_unit(
    db: AsyncSession,
    fact_type: str,
    text: str,
    source_document: str = "manual",
) -> FactUnit:
    """
    Manually add a new FactUnit.
    FR-1.3: User can add/edit facts; this is the sole creation path.
    """
    if fact_type not in FACT_TYPE_VALUES:
        raise ValueError(f"fact_type must be one of {FACT_TYPE_VALUES}, got: {fact_type!r}")

    fact_id = await _next_fact_id(db)
    unit = FactUnit(
        fact_id=fact_id,
        type=fact_type,
        text=text,
        source_document=source_document,
    )
    db.add(unit)
    await db.commit()
    await db.refresh(unit)
    return unit


async def update_fact_unit(
    db: AsyncSession,
    fact_id: str,
    text: str | None = None,
    fact_type: str | None = None,
) -> FactUnit | None:
    unit = await get_fact(db, fact_id)
    if not unit:
        return None
    if text is not None:
        unit.text = text
    if fact_type is not None:
        if fact_type not in FACT_TYPE_VALUES:
            raise ValueError(f"fact_type must be one of {FACT_TYPE_VALUES}")
        unit.type = fact_type
    await db.commit()
    await db.refresh(unit)
    return unit


async def delete_fact_unit(db: AsyncSession, fact_id: str) -> bool:
    unit = await get_fact(db, fact_id)
    if not unit:
        return False
    await db.delete(unit)
    await db.commit()
    return True


# ─── Verifier helper ──────────────────────────────────────────────────────────

async def resolve_facts(db: AsyncSession, fact_ids: list[str]) -> list[FactUnit]:
    """
    Look up multiple FactUnits by ID in a single query.
    Used by the Grounding Verifier (read-only access).
    Returns only the facts that exist; missing IDs are silently omitted
    (the verifier will catch missing IDs as a grounding failure).
    """
    if not fact_ids:
        return []
    result = await db.execute(
        select(FactUnit).where(FactUnit.fact_id.in_(fact_ids))
    )
    return list(result.scalars().all())
