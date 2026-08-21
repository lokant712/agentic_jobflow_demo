"""Profile API routes — FR-1.x"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.database import get_db
from backend.app.services import profile_service

router = APIRouter(prefix="/api/profile", tags=["profile"])


class IngestRequest(BaseModel):
    raw_text: str
    source_document: str = "resume"


class FactUnitCreate(BaseModel):
    type: str
    text: str
    source_document: str = "manual"


class FactUnitUpdate(BaseModel):
    text: str | None = None
    type: str | None = None


@router.post("/ingest", summary="Ingest raw resume text into FactUnits")
async def ingest_resume(req: IngestRequest, db: AsyncSession = Depends(get_db)):
    facts = await profile_service.ingest_resume(db, req.raw_text, req.source_document)
    return {"created": len(facts), "facts": [f.to_dict() for f in facts]}


@router.get("/facts", summary="List all FactUnits")
async def list_facts(db: AsyncSession = Depends(get_db)):
    facts = await profile_service.list_facts(db)
    return {"count": len(facts), "facts": [f.to_dict() for f in facts]}


@router.post("/facts", summary="Manually create a FactUnit")
async def create_fact(req: FactUnitCreate, db: AsyncSession = Depends(get_db)):
    fact = await profile_service.create_fact_unit(db, req.type, req.text, req.source_document)
    return fact.to_dict()


@router.put("/facts/{fact_id}", summary="Update a FactUnit")
async def update_fact(fact_id: str, req: FactUnitUpdate, db: AsyncSession = Depends(get_db)):
    fact = await profile_service.update_fact_unit(db, fact_id, req.text, req.type)
    if not fact:
        raise HTTPException(status_code=404, detail=f"FactUnit {fact_id} not found")
    return fact.to_dict()


@router.delete("/facts/{fact_id}", summary="Delete a FactUnit")
async def delete_fact(fact_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await profile_service.delete_fact_unit(db, fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"FactUnit {fact_id} not found")
    return {"deleted": fact_id}
