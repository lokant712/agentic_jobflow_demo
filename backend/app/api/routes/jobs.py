"""Jobs API routes — FR-2.x, FR-3.x, FR-4.x"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.services.ingestion.canonicalizer import canonicalize_job, list_jobs, get_job
from backend.app.services.ingestion.scout_agent import scout_jobs, scrape_job_url
from backend.app.services.ingestion.gmail_adapter import fetch_and_classify_emails, extract_job_from_email
from backend.app.db.models import ApplicationRecord
from datetime import datetime, timezone

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class ScoutRequest(BaseModel):
    title: str
    location: str
    keywords: list[str] = []
    limit: int = 20


class ManualJobRequest(BaseModel):
    company: str
    role: str
    jd_text: str
    application_link: str = ""


class ScrapeUrlRequest(BaseModel):
    url: str
    role: str = ""
    company: str = ""


@router.post("/scout", summary="Search for jobs via Scout Agent")
async def scout(req: ScoutRequest, db: AsyncSession = Depends(get_db)):
    raw_results = await scout_jobs(req.title, req.location, req.keywords, req.limit)
    results = []
    for raw in raw_results:
        record, is_new = await canonicalize_job(
            db,
            company=raw.company,
            role=raw.role or req.title,
            jd_text=raw.jd_text,
            source_channel="scout_agent",
            application_link=raw.application_link,
        )
        if is_new:
            # Create initial ApplicationRecord
            db.add(ApplicationRecord(
                job_fingerprint=record.fingerprint,
                status="discovered",
                updated_at=datetime.now(timezone.utc),
            ))
            await db.commit()
        results.append({"record": record.to_dict(), "is_new": is_new})
    return {"searched": len(raw_results), "results": results}


@router.post("/scrape", summary="Scrape a single job URL")
async def scrape(req: ScrapeUrlRequest, db: AsyncSession = Depends(get_db)):
    raw = await scrape_job_url(req.url)
    if not raw:
        raise HTTPException(status_code=422, detail="Failed to extract job content from URL")
    record, is_new = await canonicalize_job(
        db,
        company=req.company or raw.company,
        role=req.role or raw.role or "Unknown Role",
        jd_text=raw.jd_text,
        source_channel="scout_agent",
        application_link=raw.application_link,
    )
    if is_new:
        db.add(ApplicationRecord(
            job_fingerprint=record.fingerprint,
            status="discovered",
            updated_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    return {"record": record.to_dict(), "is_new": is_new}


@router.post("/gmail-sync", summary="Sync job alerts from Gmail")
async def gmail_sync(db: AsyncSession = Depends(get_db)):
    job_emails, all_classifications = await fetch_and_classify_emails(max_results=50)
    ingested = []
    for email in job_emails:
        job_kwargs = extract_job_from_email(email)
        record, is_new = await canonicalize_job(db, **job_kwargs)
        if is_new:
            db.add(ApplicationRecord(
                job_fingerprint=record.fingerprint,
                status="discovered",
                updated_at=datetime.now(timezone.utc),
            ))
            await db.commit()
        ingested.append({"record": record.to_dict(), "is_new": is_new})
    return {
        "emails_assessed": len(all_classifications),
        "job_emails_found": len(job_emails),
        "ingested": len(ingested),
        "results": ingested,
    }


@router.post("/manual", summary="Manually add a job record")
async def add_manual(req: ManualJobRequest, db: AsyncSession = Depends(get_db)):
    record, is_new = await canonicalize_job(
        db,
        company=req.company,
        role=req.role,
        jd_text=req.jd_text,
        source_channel="manual",
        application_link=req.application_link,
    )
    if is_new:
        db.add(ApplicationRecord(
            job_fingerprint=record.fingerprint,
            status="discovered",
            updated_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    return {"record": record.to_dict(), "is_new": is_new}


@router.get("", summary="List all canonical job records")
async def list_all(limit: int = 100, offset: int = 0, db: AsyncSession = Depends(get_db)):
    jobs = await list_jobs(db, limit=limit, offset=offset)
    return {"count": len(jobs), "jobs": [j.to_dict() for j in jobs]}


@router.get("/{fingerprint}", summary="Get a job by fingerprint")
async def get_one(fingerprint: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, fingerprint)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()
