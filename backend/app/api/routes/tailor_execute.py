"""
Tailor & Execute API routes — FR-5.x, FR-6.x, FR-7.x, FR-8.x, FR-9.x
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.db.models import ApplicationRecord, DecisionLog, TailoredResume
from backend.app.services.ingestion.canonicalizer import get_job
from backend.app.services.tailoring.tailor_agent import tailor_resume
from backend.app.services.tailoring.grounding_verifier import verify_resume
from backend.app.services.tailoring.pdf_generator import generate_resume_pdf
from backend.app.services.decision_engine import (
    compute_completeness_score,
    compute_execution_score,
    make_routing_decision,
    record_decision,
)
from backend.app.services.execution.path_a_autofill import run_path_a
from backend.app.services.execution.path_b_telegram import send_path_b_notification
from datetime import datetime, timezone
from sqlalchemy import select

log = logging.getLogger("jobflow.api.tailor")
router = APIRouter(prefix="/api", tags=["tailor", "execute"])


class ProfileMetadata(BaseModel):
    """Candidate profile metadata required for PDF generation and ATS form filling."""
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    current_company: str = ""
    resume_pdf_path: str = ""
    education: list[dict] = []
    skills: list[str] = []


class TailorRequest(BaseModel):
    profile: ProfileMetadata


class ExecutePathARequest(BaseModel):
    profile: ProfileMetadata


class ExecutePathBRequest(BaseModel):
    profile: ProfileMetadata


class UpdateOutcomeRequest(BaseModel):
    outcome: str
    status: str | None = None


@router.post("/tailor/{fingerprint}", summary="Tailor resume + run Grounding Verifier")
async def tailor(
    fingerprint: str,
    req: TailorRequest,
    db: AsyncSession = Depends(get_db),
):
    job = await get_job(db, fingerprint)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Run Tailor Agent
    try:
        resume = await tailor_resume(db, job)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Run Grounding Verifier (independent call)
    verification = await verify_resume(db, resume)

    # Generate PDF
    profile_dict = req.profile.model_dump()
    if not profile_dict.get("full_name"):
        profile_dict["full_name"] = f"{profile_dict.get('first_name','')} {profile_dict.get('last_name','')}".strip()

    try:
        pdf_path = generate_resume_pdf(
            resume=resume,
            profile=profile_dict,
            company=job.company,
            role=job.role,
        )
        resume.pdf_path = pdf_path
    except Exception as exc:
        log.warning(f"PDF generation failed: {exc}")

    # Persist resume
    db.add(resume)

    # Update ApplicationRecord status
    existing = await db.execute(
        select(ApplicationRecord).where(ApplicationRecord.job_fingerprint == fingerprint)
    )
    app_record = existing.scalar_one_or_none()
    if app_record:
        app_record.status = "tailored"
        app_record.updated_at = datetime.now(timezone.utc)
    else:
        db.add(ApplicationRecord(
            job_fingerprint=fingerprint,
            status="tailored",
            updated_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    await db.refresh(resume)

    return {
        "resume_id": resume.resume_id,
        "job_fingerprint": fingerprint,
        "grounding_score": verification.grounding_score,
        "total_bullets": verification.total_bullets,
        "passed_bullets": verification.passed_bullets,
        "dropped_bullets": verification.dropped_bullets,
        "verified_bullets": verification.verified_bullets,
        "pdf_path": resume.pdf_path,
        "verification_detail": [
            {
                "bullet_text": r.bullet_text[:100],
                "passed": r.passed,
                "reason": r.reason,
                "entities_checked": r.entities_checked,
                "failed_entities": r.failed_entities,
            }
            for r in verification.bullet_results
        ],
    }


@router.post("/execute/path-a/{fingerprint}", summary="Launch Path A Playwright auto-fill")
async def execute_path_a(
    fingerprint: str,
    req: ExecutePathARequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    job = await get_job(db, fingerprint)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.application_link:
        raise HTTPException(status_code=422, detail="Job has no application link")

    # Get latest tailored resume
    result = await db.execute(
        select(TailoredResume)
        .where(TailoredResume.job_fingerprint == fingerprint)
        .order_by(TailoredResume.created_at.desc())
    )
    resume = result.scalars().first()
    if not resume:
        raise HTTPException(status_code=422, detail="No tailored resume found. Run /tailor first.")

    profile_dict = req.profile.model_dump()
    if not profile_dict.get("full_name"):
        profile_dict["full_name"] = f"{profile_dict.get('first_name','')} {profile_dict.get('last_name','')}".strip()
    if resume.pdf_path:
        profile_dict["resume_pdf_path"] = resume.pdf_path

    # Compute pre-fill scores
    completeness_score, completeness_reason = compute_completeness_score(profile_dict, job.ats_type)
    execution_signals = compute_execution_score(job.ats_type)

    decision = make_routing_decision(
        grounding_score=resume.grounding_score,
        completeness_score=completeness_score,
        execution_score=execution_signals.score,
    )

    if decision.route == "PATH_B":
        log.info(f"Pre-fill gate failed → routing to PATH_B: {decision.reason}")
        await record_decision(db, job, decision)
        return {
            "route": "PATH_B",
            "reason": decision.reason,
            "scores": {
                "grounding": resume.grounding_score,
                "completeness": completeness_score,
                "execution": execution_signals.score,
            },
        }

    # Log decision
    await record_decision(db, job, decision)

    # Launch Path A in background (browser stays open for user)
    background_tasks.add_task(
        run_path_a,
        application_url=job.application_link,
        ats_type=job.ats_type,
        profile=profile_dict,
        resume=resume,
    )

    return {
        "route": "PATH_A",
        "message": "Browser auto-fill launched. Review all fields before submitting.",
        "scores": {
            "grounding": resume.grounding_score,
            "completeness": completeness_score,
            "execution": execution_signals.score,
        },
    }


@router.post("/execute/path-b/{fingerprint}", summary="Send Path B Telegram notification")
async def execute_path_b(
    fingerprint: str,
    req: ExecutePathBRequest,
    db: AsyncSession = Depends(get_db),
):
    job = await get_job(db, fingerprint)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(
        select(TailoredResume)
        .where(TailoredResume.job_fingerprint == fingerprint)
        .order_by(TailoredResume.created_at.desc())
    )
    resume = result.scalars().first()
    if not resume:
        raise HTTPException(status_code=422, detail="No tailored resume found. Run /tailor first.")

    profile_dict = req.profile.model_dump()
    completeness_score, _ = compute_completeness_score(profile_dict, job.ats_type)
    execution_signals = compute_execution_score(job.ats_type)
    decision = make_routing_decision(
        grounding_score=resume.grounding_score,
        completeness_score=completeness_score,
        execution_score=execution_signals.score,
    )
    # Override route to PATH_B for explicit call
    decision.route = "PATH_B"
    await record_decision(db, job, decision)

    sent = await send_path_b_notification(
        company=job.company,
        role=job.role,
        application_link=job.application_link or "",
        decision=decision,
        resume=resume,
    )

    return {"sent": sent, "route": "PATH_B"}


@router.get("/tracker", summary="Application tracker — all application records")
async def tracker(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ApplicationRecord).order_by(ApplicationRecord.updated_at.desc())
    )
    records = list(result.scalars().all())
    return {"count": len(records), "applications": [r.to_dict() for r in records]}


@router.patch("/tracker/{fingerprint}", summary="Update application outcome (user-reported)")
async def update_outcome(
    fingerprint: str,
    req: UpdateOutcomeRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApplicationRecord).where(ApplicationRecord.job_fingerprint == fingerprint)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Application record not found")
    record.user_outcome = req.outcome
    if req.status:
        record.status = req.status
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return record.to_dict()


@router.get("/decisions", summary="Decision Engine audit log")
async def decisions(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DecisionLog).order_by(DecisionLog.timestamp.desc()).limit(limit)
    )
    logs = list(result.scalars().all())
    return {"count": len(logs), "decisions": [d.to_dict() for d in logs]}
