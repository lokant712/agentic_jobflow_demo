"""
Canonicalization Layer — FR-4.x

All job records (any source) are normalized into CanonicalJobRecord.
Fingerprint: SHA-256(company_norm + "|" + role_norm + "|" + jd_core_hash)
Deduplication: skip entire pipeline if fingerprint already in DB.
Source confidence: fraction of required fields present (0.0 – 1.0).
ATS type detection: inspects application_link domain.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import CanonicalJobRecord


# ─── Normalization ─────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation edges."""
    return re.sub(r"\s+", " ", s.lower().strip())


def jd_core(jd_text: str) -> str:
    """
    Canonical JD core: first 2000 chars, whitespace-normalized.
    Consistent with the implementation plan fingerprint formula.
    """
    return normalize(jd_text[:2000])


# ─── Fingerprint ───────────────────────────────────────────────────────────────

def compute_fingerprint(company: str, role: str, jd_text: str) -> str:
    """
    SHA-256(company_norm + "|" + role_norm + "|" + jd_core)
    This is the dedup key — identical fingerprint = same job posting.
    """
    payload = normalize(company) + "|" + normalize(role) + "|" + jd_core(jd_text)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Source Confidence Score ───────────────────────────────────────────────────

_REQUIRED_FIELDS = ("company", "role", "jd_text", "application_link")


def compute_source_confidence(company: str, role: str, jd_text: str, application_link: str) -> float:
    """
    Score = fraction of required fields that are non-empty.
    Each of (company, role, jd_text, application_link) contributes 0.25.
    """
    values = (company, role, jd_text, application_link)
    present = sum(1 for v in values if v and v.strip())
    return round(present / len(_REQUIRED_FIELDS), 2)


# ─── ATS Type Detection ────────────────────────────────────────────────────────

_ATS_DOMAIN_MAP = {
    "greenhouse.io": "greenhouse",
    "grnh.se": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "ashbyhq.com": "ashby",
    "jobs.ashbyhq.com": "ashby",
}


def detect_ats_type(application_link: str) -> str:
    """
    Inspect application URL domain to determine ATS platform.
    Returns one of: "greenhouse" | "lever" | "ashby" | "other"
    """
    if not application_link:
        return "other"
    link_lower = application_link.lower()
    for domain, ats in _ATS_DOMAIN_MAP.items():
        if domain in link_lower:
            return ats
    return "other"


# ─── Canonicalize & Dedup ─────────────────────────────────────────────────────

async def canonicalize_job(
    db: AsyncSession,
    *,
    company: str,
    role: str,
    jd_text: str,
    source_channel: str,
    application_link: str = "",
) -> tuple[CanonicalJobRecord, bool]:
    """
    Normalize, fingerprint, dedup, and persist a job record.

    Returns:
        (record, is_new): is_new=False means duplicate was detected and
        the existing record is returned without re-processing.

    FR-4.1: Normalized into CanonicalJobRecord schema.
    FR-4.2: Fingerprint computed from normalized fields.
    FR-4.3: Deduplicated by fingerprint across all sources.
    FR-4.4: Source confidence score assigned.
    """
    # Normalize inputs
    company = company.strip()
    role = role.strip()
    jd_text = jd_text.strip()
    application_link = (application_link or "").strip()

    # Compute canonical identifiers
    fingerprint = compute_fingerprint(company, role, jd_text)
    confidence = compute_source_confidence(company, role, jd_text, application_link)
    ats_type = detect_ats_type(application_link)

    # Dedup check (FR-4.3)
    existing = await db.execute(
        select(CanonicalJobRecord).where(CanonicalJobRecord.fingerprint == fingerprint)
    )
    existing_record = existing.scalar_one_or_none()
    if existing_record:
        return existing_record, False

    # Persist new record
    record = CanonicalJobRecord(
        fingerprint=fingerprint,
        company=company,
        role=role,
        jd_text=jd_text,
        source_channel=source_channel,
        source_confidence=confidence,
        application_link=application_link,
        ats_type=ats_type,
        first_seen_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record, True


async def list_jobs(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
) -> list[CanonicalJobRecord]:
    result = await db.execute(
        select(CanonicalJobRecord)
        .order_by(CanonicalJobRecord.first_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_job(db: AsyncSession, fingerprint: str) -> CanonicalJobRecord | None:
    result = await db.execute(
        select(CanonicalJobRecord).where(CanonicalJobRecord.fingerprint == fingerprint)
    )
    return result.scalar_one_or_none()
