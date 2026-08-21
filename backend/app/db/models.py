"""
SQLAlchemy ORM models for all five core Agentic-JobFlow entities.
Each model maps 1:1 to the data model defined in the PRD §9.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# FactUnit
# PRD §7.1 — Master Profile Store
# One atomic claim per unit: responsibility, metric, tool, or outcome.
# Immutable IDs (FACT-001, FACT-002, …), source-of-truth is human-editable.
# ─────────────────────────────────────────────────────────────────────────────

FACT_TYPE_VALUES = ("responsibility", "metric", "tool", "outcome")


class FactUnit(Base):
    __tablename__ = "fact_units"

    fact_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # one of FACT_TYPE_VALUES
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_document: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "type": self.type,
            "text": self.text,
            "source_document": self.source_document,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# CanonicalJobRecord
# PRD §7.4 — Canonicalization Layer
# Fingerprint = SHA-256(company_norm | role_norm | jd_core_hash)
# ─────────────────────────────────────────────────────────────────────────────

ATS_TYPE_VALUES = ("greenhouse", "lever", "ashby", "other")
SOURCE_CHANNEL_VALUES = ("scout_agent", "gmail", "manual")


class CanonicalJobRecord(Base):
    __tablename__ = "canonical_job_records"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    application_link: Mapped[str] = mapped_column(Text, nullable=True)
    ats_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    # Relationships
    tailored_resumes: Mapped[list["TailoredResume"]] = relationship(
        "TailoredResume", back_populates="job", cascade="all, delete-orphan"
    )
    decision_logs: Mapped[list["DecisionLog"]] = relationship(
        "DecisionLog", back_populates="job", cascade="all, delete-orphan"
    )
    application_record: Mapped["ApplicationRecord | None"] = relationship(
        "ApplicationRecord", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "company": self.company,
            "role": self.role,
            "jd_text": self.jd_text,
            "source_channel": self.source_channel,
            "source_confidence": self.source_confidence,
            "application_link": self.application_link,
            "ats_type": self.ats_type,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TailoredResume
# PRD §7.5 — Tailor Agent output
# bullets is a JSON array of {text: str, fact_ids: list[str]}
# ─────────────────────────────────────────────────────────────────────────────


class TailoredResume(Base):
    __tablename__ = "tailored_resumes"

    resume_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_fingerprint: Mapped[str] = mapped_column(
        String(64), ForeignKey("canonical_job_records.fingerprint"), nullable=False
    )
    bullets: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    grounding_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pdf_path: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    job: Mapped["CanonicalJobRecord"] = relationship(
        "CanonicalJobRecord", back_populates="tailored_resumes"
    )

    def get_bullets(self) -> list[dict]:
        """Deserialize bullets JSON to Python list."""
        return json.loads(self.bullets)

    def set_bullets(self, bullets: list[dict]) -> None:
        """Serialize bullets list to JSON string."""
        self.bullets = json.dumps(bullets)

    def to_dict(self) -> dict:
        return {
            "resume_id": self.resume_id,
            "job_fingerprint": self.job_fingerprint,
            "bullets": self.get_bullets(),
            "grounding_score": self.grounding_score,
            "pdf_path": self.pdf_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# DecisionLog
# PRD §7.7 — Decision Engine audit trail
# route: "PATH_A" | "PATH_B"
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_VALUES = ("PATH_A", "PATH_B")


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_fingerprint: Mapped[str] = mapped_column(
        String(64), ForeignKey("canonical_job_records.fingerprint"), nullable=False
    )
    grounding_score: Mapped[float] = mapped_column(Float, nullable=False)
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False)
    execution_score: Mapped[float] = mapped_column(Float, nullable=False)
    route: Mapped[str] = mapped_column(String(8), nullable=False)  # PATH_A | PATH_B
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    job: Mapped["CanonicalJobRecord"] = relationship(
        "CanonicalJobRecord", back_populates="decision_logs"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_fingerprint": self.job_fingerprint,
            "grounding_score": self.grounding_score,
            "completeness_score": self.completeness_score,
            "execution_score": self.execution_score,
            "route": self.route,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ApplicationRecord
# PRD §7.10 — Application Tracking
# status progression: discovered → tailored → routed_a/routed_b → submitted (user-reported)
# ─────────────────────────────────────────────────────────────────────────────

APPLICATION_STATUS_VALUES = (
    "discovered",
    "tailored",
    "routed_a",
    "routed_b",
    "submitted",
    "rejected",
    "interviewing",
    "offered",
    "declined",
)


class ApplicationRecord(Base):
    __tablename__ = "application_records"

    job_fingerprint: Mapped[str] = mapped_column(
        String(64), ForeignKey("canonical_job_records.fingerprint"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    path_taken: Mapped[str] = mapped_column(String(8), nullable=True)  # PATH_A | PATH_B | None
    user_outcome: Mapped[str] = mapped_column(Text, nullable=True)  # free text, manual input
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    job: Mapped["CanonicalJobRecord"] = relationship(
        "CanonicalJobRecord", back_populates="application_record"
    )

    def to_dict(self) -> dict:
        return {
            "job_fingerprint": self.job_fingerprint,
            "status": self.status,
            "path_taken": self.path_taken,
            "user_outcome": self.user_outcome,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
