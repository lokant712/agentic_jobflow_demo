"""Tests for Canonicalization Layer (canonicalizer.py)"""
import pytest
from backend.app.services.ingestion.canonicalizer import (
    normalize, jd_core, compute_fingerprint,
    compute_source_confidence, detect_ats_type,
)


# ─── Normalization ────────────────────────────────────────────────────────────

def test_normalize_lowercases_and_collapses_whitespace():
    assert normalize("  Acme   Corp  ") == "acme corp"
    assert normalize("SENIOR ENGINEER") == "senior engineer"


def test_normalize_strips_edges():
    assert normalize("\n  SWE  \t") == "swe"


# ─── Fingerprint ─────────────────────────────────────────────────────────────

def test_fingerprint_is_64_char_hex():
    fp = compute_fingerprint("Acme", "Software Engineer", "We are looking for a SWE")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_deterministic():
    fp1 = compute_fingerprint("Acme Corp", "Software Engineer", "Job desc text here")
    fp2 = compute_fingerprint("Acme Corp", "Software Engineer", "Job desc text here")
    assert fp1 == fp2


def test_fingerprint_different_for_different_roles():
    fp1 = compute_fingerprint("Acme", "Backend Engineer", "Build APIs")
    fp2 = compute_fingerprint("Acme", "Frontend Engineer", "Build APIs")
    assert fp1 != fp2


def test_fingerprint_whitespace_invariant():
    """Same content with different whitespace must produce same fingerprint (dedup invariant)."""
    fp1 = compute_fingerprint("Acme Corp", "Software  Engineer", "We   are hiring")
    fp2 = compute_fingerprint("Acme Corp", "Software Engineer", "We are hiring")
    assert fp1 == fp2


def test_fingerprint_case_invariant():
    """Case differences must NOT produce different fingerprints."""
    fp1 = compute_fingerprint("ACME CORP", "SOFTWARE ENGINEER", "WE ARE HIRING")
    fp2 = compute_fingerprint("Acme Corp", "Software Engineer", "We Are Hiring")
    assert fp1 == fp2


def test_fingerprint_same_job_from_different_sources():
    """
    FR-4.3: Dedup invariant — same job posted via Scout Agent and Gmail
    must produce identical fingerprints.
    """
    jd = "We are looking for a Senior Python Engineer to join our data team."
    fp_scout = compute_fingerprint("Acme Inc", "Senior Python Engineer", jd)
    fp_gmail = compute_fingerprint("Acme Inc", "Senior Python Engineer", jd)
    assert fp_scout == fp_gmail, "Duplicate job from different channels must dedup to same fingerprint"


def test_fingerprint_different_company_same_role():
    fp1 = compute_fingerprint("Acme", "Data Engineer", "Build pipelines")
    fp2 = compute_fingerprint("Beta Corp", "Data Engineer", "Build pipelines")
    assert fp1 != fp2


# ─── Source Confidence ───────────────────────────────────────────────────────

def test_confidence_all_fields_present():
    score = compute_source_confidence("Acme", "SWE", "We are hiring", "https://jobs.greenhouse.io/acme")
    assert score == 1.0


def test_confidence_missing_link():
    score = compute_source_confidence("Acme", "SWE", "We are hiring", "")
    assert score == 0.75


def test_confidence_only_company_and_role():
    score = compute_source_confidence("Acme", "SWE", "", "")
    assert score == 0.5


def test_confidence_all_missing():
    score = compute_source_confidence("", "", "", "")
    assert score == 0.0


# ─── ATS Type Detection ──────────────────────────────────────────────────────

def test_detect_greenhouse():
    assert detect_ats_type("https://jobs.greenhouse.io/acme/123") == "greenhouse"
    assert detect_ats_type("https://boards.greenhouse.io/company/jobs/456") == "greenhouse"


def test_detect_lever():
    assert detect_ats_type("https://jobs.lever.co/company/abc-123") == "lever"


def test_detect_ashby():
    assert detect_ats_type("https://jobs.ashbyhq.com/company/role") == "ashby"


def test_detect_other():
    assert detect_ats_type("https://careers.unknown-company.com/apply") == "other"
    assert detect_ats_type("") == "other"


# ─── Integration: canonicalize + dedup ───────────────────────────────────────

@pytest.mark.asyncio
async def test_canonicalize_dedup_returns_existing_on_duplicate():
    """FR-4.3: Second canonicalize call with same content returns existing record, is_new=False."""
    from unittest.mock import AsyncMock, MagicMock
    from backend.app.services.ingestion.canonicalizer import canonicalize_job
    from backend.app.db.models import CanonicalJobRecord

    existing = CanonicalJobRecord(
        fingerprint="abc123",
        company="Acme",
        role="SWE",
        jd_text="We are hiring",
        source_channel="scout_agent",
        source_confidence=1.0,
        ats_type="other",
    )

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=mock_result)

    record, is_new = await canonicalize_job(
        db,
        company="Acme",
        role="SWE",
        jd_text="We are hiring",
        source_channel="gmail",
    )
    assert is_new is False
    assert record.fingerprint == "abc123"
    # db.add should NOT be called for duplicates
    db.add.assert_not_called()
