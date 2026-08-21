"""
Decision Engine — FR-7.x

3-Signal Gate: evaluates Grounding Score, Completeness Score, and Execution Score.
Routes to PATH_A (Playwright auto-fill) or PATH_B (Telegram fallback).
All decisions are logged to the DecisionLog table.

Scores and thresholds:

  Grounding Score:
    Source: grounding_verifier output
    = verified_bullets / total_bullets
    Threshold: >= 0.95 (configurable)

  Completeness Score:
    Denominator: per-ATS required-fields manifest (pre-computed before Playwright opens)
    = profile_fields_mappable / required_fields_in_manifest
    Threshold: >= 0.85 (configurable)
    ATS type "other" → score = 0.0 → auto PATH_B

  Execution Score:
    Three binary signals (each 0 or 1):
      Signal 1: ATS type is greenhouse | lever | ashby (not "other")
      Signal 2: No bot-challenge indicators detected on the page snapshot
      Signal 3: All manifest selectors resolve to visible elements within 10s
    Score = (s1 + s2 + s3) / 3
    Threshold: >= 0.90 (requires all 3 signals to pass since 2/3 = 0.67 < 0.90)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import (
    ApplicationRecord,
    CanonicalJobRecord,
    DecisionLog,
    TailoredResume,
)

log = logging.getLogger("jobflow.decision_engine")

Route = Literal["PATH_A", "PATH_B"]
_MANIFESTS_DIR = Path(__file__).parent / "execution" / "ats_manifests"


# ─── Manifest loader ──────────────────────────────────────────────────────────

def _load_manifest(ats_type: str) -> dict | None:
    """Load the required-fields manifest for the given ATS type."""
    manifest_path = _MANIFESTS_DIR / f"{ats_type}.json"
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get(ats_type)


# ─── Score Computation ────────────────────────────────────────────────────────

def compute_completeness_score(
    profile: dict,
    ats_type: str,
) -> tuple[float, str]:
    """
    Compute Completeness Score pre-fill using the ATS manifest.
    Denominator is the manifest's required_fields list (required=True only).

    Returns (score, reason).
    ATS type "other" → (0.0, "ats_type_other_no_manifest").
    """
    if ats_type == "other" or ats_type not in ("greenhouse", "lever", "ashby"):
        return 0.0, "ats_type_other_no_manifest"

    manifest = _load_manifest(ats_type)
    if not manifest:
        return 0.0, f"manifest_not_found:{ats_type}"

    required_fields = [
        f for f in manifest.get("required_fields", [])
        if f.get("required", True)  # default required=True unless explicitly False
    ]
    if not required_fields:
        return 1.0, "no_required_fields_in_manifest"

    mappable = 0
    missing = []
    for field_spec in required_fields:
        profile_key = field_spec.get("profile_key", "")
        field_type = field_spec.get("type", "")
        field_id = field_spec.get("id", "")

        value = profile.get(profile_key, "")
        if field_type == "file":
            # Resume file: check if pdf_path is set
            value = profile.get("resume_pdf_path", "") or profile.get(profile_key, "")
        if value and str(value).strip():
            mappable += 1
        else:
            missing.append(field_id)

    score = round(mappable / len(required_fields), 4)
    reason = (
        "all_required_fields_mappable"
        if not missing
        else f"missing_profile_fields:{missing}"
    )
    return score, reason


@dataclass
class ExecutionSignals:
    ats_identified: bool        # Signal 1
    no_bot_challenge: bool      # Signal 2
    selectors_stable: bool      # Signal 3
    score: float
    details: dict


def compute_execution_score(
    ats_type: str,
    bot_challenge_detected: bool = False,
    selectors_resolved: bool = True,
) -> ExecutionSignals:
    """
    Compute Execution Score from three binary signals.
    Called BEFORE Playwright opens (for signals 1 only) and updated
    mid-fill as signals 2 and 3 are resolved.
    """
    s1 = ats_type in ("greenhouse", "lever", "ashby")
    s2 = not bot_challenge_detected
    s3 = selectors_resolved

    score = round((int(s1) + int(s2) + int(s3)) / 3, 4)
    return ExecutionSignals(
        ats_identified=s1,
        no_bot_challenge=s2,
        selectors_stable=s3,
        score=score,
        details={
            "signal_1_ats_identified": s1,
            "signal_2_no_bot_challenge": s2,
            "signal_3_selectors_stable": s3,
            "ats_type": ats_type,
        },
    )


# ─── Routing Decision ─────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    route: Route
    grounding_score: float
    completeness_score: float
    execution_score: float
    reason: str
    threshold_grounding: float
    threshold_completeness: float
    threshold_execution: float


def make_routing_decision(
    grounding_score: float,
    completeness_score: float,
    execution_score: float,
    settings=None,
) -> RoutingDecision:
    """
    FR-7.1 – FR-7.3: Evaluate all three scores against configurable thresholds.
    Routes to PATH_A only if ALL three scores clear simultaneously.
    Any single failure → PATH_B. Reason logged is the specific failing score.
    """
    if settings is None:
        settings = get_settings()

    t_grounding = settings.threshold_grounding
    t_completeness = settings.threshold_completeness
    t_execution = settings.threshold_execution

    failures = []
    if grounding_score < t_grounding:
        failures.append(f"grounding_score={grounding_score:.3f}<{t_grounding}")
    if completeness_score < t_completeness:
        failures.append(f"completeness_score={completeness_score:.3f}<{t_completeness}")
    if execution_score < t_execution:
        failures.append(f"execution_score={execution_score:.3f}<{t_execution}")

    if failures:
        route = "PATH_B"
        reason = "; ".join(failures)
    else:
        route = "PATH_A"
        reason = "all_scores_above_threshold"

    return RoutingDecision(
        route=route,
        grounding_score=grounding_score,
        completeness_score=completeness_score,
        execution_score=execution_score,
        reason=reason,
        threshold_grounding=t_grounding,
        threshold_completeness=t_completeness,
        threshold_execution=t_execution,
    )


# ─── Persist & Update Application Record ──────────────────────────────────────

async def record_decision(
    db: AsyncSession,
    job: CanonicalJobRecord,
    decision: RoutingDecision,
) -> DecisionLog:
    """
    FR-7.4: Persist routing decision to DecisionLog with scores, thresholds, and timestamp.
    Also creates or updates ApplicationRecord with route status.
    """
    log_entry = DecisionLog(
        id=str(uuid.uuid4()),
        job_fingerprint=job.fingerprint,
        grounding_score=decision.grounding_score,
        completeness_score=decision.completeness_score,
        execution_score=decision.execution_score,
        route=decision.route,
        reason=decision.reason,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(log_entry)

    # Create or update ApplicationRecord
    from sqlalchemy import select
    existing_app = await db.execute(
        select(ApplicationRecord).where(
            ApplicationRecord.job_fingerprint == job.fingerprint
        )
    )
    app_record = existing_app.scalar_one_or_none()

    new_status = "routed_a" if decision.route == "PATH_A" else "routed_b"
    if app_record:
        app_record.status = new_status
        app_record.path_taken = decision.route
        app_record.updated_at = datetime.now(timezone.utc)
    else:
        app_record = ApplicationRecord(
            job_fingerprint=job.fingerprint,
            status=new_status,
            path_taken=decision.route,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(app_record)

    await db.commit()

    log.info(
        f"Decision Engine: {job.company}/{job.role} → {decision.route} "
        f"(G={decision.grounding_score:.3f} C={decision.completeness_score:.3f} "
        f"E={decision.execution_score:.3f}) reason={decision.reason}"
    )
    return log_entry
