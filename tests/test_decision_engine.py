"""
Tests for Decision Engine (decision_engine.py)

Covers:
  - Completeness Score (all present, missing link, ATS type "other")
  - Execution Score (all signals pass, individual signal failures)
  - Routing decisions (PATH_A when all clear, PATH_B on any single failure)
  - Reason logging accuracy
  - ATS detection routing
"""
import pytest
from backend.app.services.decision_engine import (
    compute_completeness_score,
    compute_execution_score,
    make_routing_decision,
    ExecutionSignals,
)
from unittest.mock import MagicMock, patch


# ─── Completeness Score ───────────────────────────────────────────────────────

def test_completeness_score_all_fields_present_greenhouse():
    profile = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+1-555-0100",
        "resume_pdf_path": "/data/resumes/resume.pdf",
        "location": "New York, NY",
        "linkedin": "https://linkedin.com/in/janedoe",
    }
    score, reason = compute_completeness_score(profile, "greenhouse")
    assert score == 1.0
    assert reason == "all_required_fields_mappable"


def test_completeness_score_missing_resume_greenhouse():
    profile = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+1-555-0100",
        "resume_pdf_path": "",  # missing PDF
        "location": "New York, NY",
        "linkedin": "https://linkedin.com/in/janedoe",
    }
    score, reason = compute_completeness_score(profile, "greenhouse")
    assert score < 1.0
    assert "missing_profile_fields" in reason


def test_completeness_score_ats_type_other_auto_zero():
    """FR-7.2: ATS type 'other' → Completeness Score = 0.0 → auto PATH_B."""
    score, reason = compute_completeness_score({}, "other")
    assert score == 0.0
    assert reason == "ats_type_other_no_manifest"


def test_completeness_score_lever_all_fields():
    profile = {
        "full_name": "John Smith",
        "email": "john@example.com",
        "phone": "+1-555-0200",
        "resume_pdf_path": "/data/resumes/resume.pdf",
        "current_company": "Previous Corp",
        "linkedin": "https://linkedin.com/in/johnsmith",
    }
    score, reason = compute_completeness_score(profile, "lever")
    assert score == 1.0


def test_completeness_score_ashby_missing_phone():
    profile = {
        "full_name": "Alice Wang",
        "email": "alice@example.com",
        "phone": "",  # missing
        "resume_pdf_path": "/data/resumes/resume.pdf",
        "linkedin": "",
    }
    score, reason = compute_completeness_score(profile, "ashby")
    assert score < 1.0
    assert "phone" in reason


# ─── Execution Score ─────────────────────────────────────────────────────────

def test_execution_score_all_signals_pass():
    signals = compute_execution_score("greenhouse", bot_challenge_detected=False, selectors_resolved=True)
    assert signals.score == 1.0
    assert signals.ats_identified is True
    assert signals.no_bot_challenge is True
    assert signals.selectors_stable is True


def test_execution_score_ats_not_identified():
    """Signal 1 fails → score = 2/3 = 0.667 < 0.90 threshold."""
    signals = compute_execution_score("other", bot_challenge_detected=False, selectors_resolved=True)
    assert signals.ats_identified is False
    assert abs(signals.score - 0.6667) < 0.001


def test_execution_score_bot_challenge_detected():
    """Signal 2 fails → score = 2/3 = 0.667."""
    signals = compute_execution_score("greenhouse", bot_challenge_detected=True, selectors_resolved=True)
    assert signals.no_bot_challenge is False
    assert abs(signals.score - 0.6667) < 0.001


def test_execution_score_selectors_not_found():
    """Signal 3 fails → score = 2/3 = 0.667."""
    signals = compute_execution_score("lever", bot_challenge_detected=False, selectors_resolved=False)
    assert signals.selectors_stable is False
    assert abs(signals.score - 0.6667) < 0.001


def test_execution_score_all_signals_fail():
    signals = compute_execution_score("other", bot_challenge_detected=True, selectors_resolved=False)
    assert signals.score == 0.0


# ─── Routing Decisions ────────────────────────────────────────────────────────

def _make_settings(grounding=0.95, completeness=0.85, execution=0.90):
    s = MagicMock()
    s.threshold_grounding = grounding
    s.threshold_completeness = completeness
    s.threshold_execution = execution
    return s


def test_route_path_a_all_scores_above_threshold():
    """FR-7.2: All scores above threshold → PATH_A."""
    decision = make_routing_decision(
        grounding_score=0.97,
        completeness_score=0.90,
        execution_score=1.0,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_A"
    assert decision.reason == "all_scores_above_threshold"


def test_route_path_b_grounding_fails():
    """FR-7.3: Grounding score below threshold → PATH_B."""
    decision = make_routing_decision(
        grounding_score=0.80,  # below 0.95
        completeness_score=0.90,
        execution_score=1.0,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_B"
    assert "grounding_score" in decision.reason


def test_route_path_b_completeness_fails():
    """FR-7.3: Completeness score below threshold → PATH_B."""
    decision = make_routing_decision(
        grounding_score=0.97,
        completeness_score=0.70,  # below 0.85
        execution_score=1.0,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_B"
    assert "completeness_score" in decision.reason


def test_route_path_b_execution_fails():
    """FR-7.3: Execution score below threshold (bot challenge) → PATH_B."""
    decision = make_routing_decision(
        grounding_score=0.97,
        completeness_score=0.90,
        execution_score=0.667,  # 2/3 signals only
        settings=_make_settings(),
    )
    assert decision.route == "PATH_B"
    assert "execution_score" in decision.reason


def test_route_path_b_ats_type_other_via_completeness():
    """ATS type 'other' → completeness=0.0 → PATH_B."""
    score, _ = compute_completeness_score({}, "other")
    decision = make_routing_decision(
        grounding_score=0.97,
        completeness_score=score,  # 0.0
        execution_score=1.0,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_B"


def test_route_exactly_at_threshold_passes():
    """Scores exactly at threshold → PATH_A (>= not >)."""
    decision = make_routing_decision(
        grounding_score=0.95,
        completeness_score=0.85,
        execution_score=0.90,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_A"


def test_route_multiple_failures_reason_lists_all():
    """All three scores fail → reason mentions all three."""
    decision = make_routing_decision(
        grounding_score=0.70,
        completeness_score=0.50,
        execution_score=0.33,
        settings=_make_settings(),
    )
    assert decision.route == "PATH_B"
    assert "grounding_score" in decision.reason
    assert "completeness_score" in decision.reason
    assert "execution_score" in decision.reason
