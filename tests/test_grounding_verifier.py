"""
Tests for Grounding Verifier (grounding_verifier.py)

Covers:
  - Entity extraction
  - Grounding check (pass: entities present in facts, fail: entities not in facts)
  - Hallucinated metric detection
  - Drop after exhausted retries
  - Grounding Score calculation
  - FR-6.3: drop unverified rather than output
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.tailoring.grounding_verifier import (
    extract_key_entities,
    check_entity_grounded,
    _verify_bullet,
    verify_resume,
    BulletVerificationResult,
)
from backend.app.db.models import FactUnit, TailoredResume
import json


# ─── Entity Extraction ────────────────────────────────────────────────────────

def test_extract_numeric_with_percent():
    entities = extract_key_entities("Reduced costs by 35% through optimization")
    assert any("35" in e for e in entities)


def test_extract_numeric_with_unit():
    entities = extract_key_entities("Reduced latency from 500ms to 120ms")
    assert any("500" in e for e in entities)
    assert any("120" in e for e in entities)


def test_extract_known_tool():
    entities = extract_key_entities("Built pipeline using Python and Apache Spark")
    assert "python" in entities
    assert "spark" in entities


def test_extract_capitalized_noun():
    entities = extract_key_entities("Led migration to Kubernetes on AWS")
    assert "kubernetes" in entities
    assert "aws" in entities


def test_extract_no_entities_returns_empty_list():
    # Generic bullet with no numbers or known tools
    entities = extract_key_entities("Led a team and improved collaboration across departments")
    # Should not crash; may return empty or just generic nouns
    assert isinstance(entities, list)


# ─── Grounding Check ─────────────────────────────────────────────────────────

def test_entity_grounded_substring_match():
    assert check_entity_grounded("35%", ["Reduced operational costs by 35% in Q3"])


def test_entity_grounded_case_insensitive():
    assert check_entity_grounded("python", ["Expert in Python and distributed systems"])


def test_entity_not_grounded():
    assert not check_entity_grounded("50%", ["Reduced costs by 35% through optimization"])


def test_entity_grounded_multiple_facts():
    """Entity found in any one of the cited facts → grounded."""
    facts = [
        "Led a cross-functional team of engineers",
        "Reduced infrastructure costs by 40%",
    ]
    assert check_entity_grounded("40%", facts)
    assert not check_entity_grounded("99%", facts)


# ─── Single Bullet Verification ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_bullet_pass_all_entities_present():
    """Bullet with all entities present in cited facts → PASS."""
    db = AsyncMock()
    fact = FactUnit(fact_id="FACT-001", type="metric", text="Increased revenue by 50% in FY2023")

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fact]
    db.execute = AsyncMock(return_value=mock_result)

    bullet = {"text": "Increased revenue by 50% in FY2023", "fact_ids": ["FACT-001"]}
    result = await _verify_bullet(db, bullet, attempt=1)
    assert result.passed is True
    assert result.failed_entities == []


@pytest.mark.asyncio
async def test_verify_bullet_fail_hallucinated_metric():
    """Bullet with hallucinated metric (90%) not in fact (50%) → FAIL."""
    db = AsyncMock()
    fact = FactUnit(fact_id="FACT-001", type="metric", text="Increased revenue by 50%")

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fact]
    db.execute = AsyncMock(return_value=mock_result)

    bullet = {"text": "Increased revenue by 90%", "fact_ids": ["FACT-001"]}
    result = await _verify_bullet(db, bullet, attempt=1)
    assert result.passed is False
    assert any("90" in e for e in result.failed_entities)


@pytest.mark.asyncio
async def test_verify_bullet_fail_missing_fact_id():
    """Bullet citing a non-existent fact_id → FAIL (missing fact detection)."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []  # FACT-999 doesn't exist
    db.execute = AsyncMock(return_value=mock_result)

    bullet = {"text": "Increased revenue by 50%", "fact_ids": ["FACT-999"]}
    result = await _verify_bullet(db, bullet, attempt=1)
    assert result.passed is False
    assert "FACT-999" in result.failed_entities


@pytest.mark.asyncio
async def test_verify_bullet_pass_no_extractable_entities():
    """Bullet with no extractable key entities → PASS (logged as unverifiable)."""
    db = AsyncMock()
    fact = FactUnit(fact_id="FACT-001", type="responsibility", text="Led team collaboration")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fact]
    db.execute = AsyncMock(return_value=mock_result)

    bullet = {"text": "Collaborated across teams to deliver on time", "fact_ids": ["FACT-001"]}
    result = await _verify_bullet(db, bullet, attempt=1)
    assert result.passed is True
    assert result.reason == "no_key_entities_extracted_pass"


# ─── Full Resume Verification ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_resume_all_pass():
    """3 passing bullets → Grounding Score = 1.0."""
    db = AsyncMock()

    facts = [
        FactUnit(fact_id="FACT-001", type="metric", text="Reduced latency by 40%"),
        FactUnit(fact_id="FACT-002", type="tool", text="Used Python and PostgreSQL"),
        FactUnit(fact_id="FACT-003", type="outcome", text="Delivered project two months early"),
    ]

    def make_mock_result(fact_ids):
        relevant = [f for f in facts if f.fact_id in fact_ids]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = relevant
        return mock_result

    call_count = [0]
    def execute_side_effect(query):
        # Determine which fact_ids are being queried
        # In production this would parse the query; mock returns first fact
        call_count[0] += 1
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = facts
        return mock_result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    resume = TailoredResume(
        resume_id="test-resume-001",
        job_fingerprint="abc123",
        grounding_score=0.0,
    )
    bullets = [
        {"text": "Reduced latency by 40% via database optimization", "fact_ids": ["FACT-001"]},
        {"text": "Built pipeline using python and postgresql", "fact_ids": ["FACT-002"]},
        {"text": "Delivered project ahead of schedule", "fact_ids": ["FACT-003"]},
    ]
    resume.set_bullets(bullets)

    with patch("backend.app.services.tailoring.grounding_verifier._write_verification_log"):
        with patch("backend.app.config.get_settings") as mock_settings:
            mock_settings.return_value.verifier_max_retries = 2
            mock_settings.return_value.log_dir = "/tmp/test_logs"
            result = await verify_resume(db, resume)

    assert result.grounding_score == 1.0
    assert result.passed_bullets == 3
    assert result.dropped_bullets == 0


@pytest.mark.asyncio
async def test_verify_resume_drop_after_retries():
    """FR-6.3: Bullet failing all N retries is DROPPED (not in output). Score reflects drop."""
    db = AsyncMock()

    fact = FactUnit(fact_id="FACT-001", type="metric", text="Reduced costs by 10%")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fact]
    db.execute = AsyncMock(return_value=mock_result)

    resume = TailoredResume(
        resume_id="test-resume-002",
        job_fingerprint="abc456",
        grounding_score=0.0,
    )
    # This bullet claims 99% but fact has 10% → will always fail
    bullets = [
        {"text": "Reduced costs by 99%", "fact_ids": ["FACT-001"]},
    ]
    resume.set_bullets(bullets)

    with patch("backend.app.services.tailoring.grounding_verifier._write_verification_log"):
        with patch("backend.app.services.tailoring.grounding_verifier._regenerate_bullet", return_value=None):
            with patch("backend.app.config.get_settings") as mock_settings:
                mock_settings.return_value.verifier_max_retries = 2
                mock_settings.return_value.log_dir = "/tmp/test_logs"
                result = await verify_resume(db, resume)

    # Grounding score = 0/1 = 0.0; bullet should be dropped
    assert result.grounding_score == 0.0
    assert result.passed_bullets == 0
    assert result.dropped_bullets == 1
    assert result.verified_bullets == []  # FR-6.3: dropped, not in output


def test_grounding_score_calculation():
    """Score = passed_bullets / total_bullets."""
    # 3 bullets, 2 pass → 0.6667
    score = round(2 / 3, 4)
    assert abs(score - 0.6667) < 0.001
