"""
PDF Generator — FR-5.3

Generates ATS-compatible PDF resumes from verified TailoredResume bullets.
Uses ReportLab for programmatic PDF creation.

ATS safety rules enforced:
  - No multi-column layouts
  - No tables for content
  - Machine-readable text layer (no image-based text)
  - Standard fonts only (Helvetica)
  - Left-aligned body text
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    HRFlowable,
)

from backend.app.config import get_settings
from backend.app.db.models import TailoredResume


# ─── Style Definitions ────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()

    name_style = ParagraphStyle(
        "CandidateName",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=8,
        textColor=colors.HexColor("#555555"),
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        spaceBefore=12,
        spaceAfter=3,
        textColor=colors.HexColor("#1a1a2e"),
    )
    job_title_style = ParagraphStyle(
        "JobTitle",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        spaceBefore=8,
        spaceAfter=1,
    )
    date_style = ParagraphStyle(
        "Date",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#666666"),
        spaceAfter=2,
    )
    bullet_style = ParagraphStyle(
        "BulletItem",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        leftIndent=14,
        spaceAfter=2,
        bulletIndent=4,
    )
    skills_style = ParagraphStyle(
        "Skills",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        spaceAfter=2,
    )

    return {
        "name": name_style,
        "contact": contact_style,
        "section_header": section_header_style,
        "job_title": job_title_style,
        "date": date_style,
        "bullet": bullet_style,
        "skills": skills_style,
    }


# ─── Header Section ───────────────────────────────────────────────────────────

def _build_header(profile: dict, styles: dict) -> list:
    """Build candidate name and contact info header."""
    elements = []

    name = profile.get("name", "Candidate Name")
    elements.append(Paragraph(name, styles["name"]))

    contact_parts = []
    for field in ("email", "phone", "location", "linkedin"):
        val = profile.get(field, "")
        if val:
            contact_parts.append(val)
    if contact_parts:
        elements.append(Paragraph(" | ".join(contact_parts), styles["contact"]))

    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a1a2e")))
    elements.append(Spacer(1, 6))
    return elements


# ─── Tailored Experience Section ──────────────────────────────────────────────

def _build_experience_section(
    company: str,
    role: str,
    bullets: list[dict],
    styles: dict,
) -> list:
    """Build the tailored experience section from verified bullets."""
    elements = []
    elements.append(Paragraph("PROFESSIONAL EXPERIENCE", styles["section_header"]))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    elements.append(Spacer(1, 4))

    elements.append(Paragraph(f"{role} — {company}", styles["job_title"]))
    elements.append(Spacer(1, 3))

    for bullet in bullets:
        text = bullet.get("text", "").strip()
        if text:
            # ATS-safe: use bullet character, left-aligned paragraph
            elements.append(Paragraph(f"• {text}", styles["bullet"]))

    return elements


# ─── Main PDF Generator ───────────────────────────────────────────────────────

def generate_resume_pdf(
    resume: TailoredResume,
    profile: dict,
    company: str,
    role: str,
    output_dir: str | None = None,
) -> str:
    """
    FR-5.3: Generate an ATS-compatible PDF from verified TailoredResume bullets.

    Args:
        resume: TailoredResume with verified bullets and grounding_score.
        profile: dict with candidate metadata (name, email, phone, location, linkedin).
        company: Target company name.
        role: Target role title.
        output_dir: Override output directory (uses settings.resume_output_dir if None).

    Returns:
        Absolute path to the generated PDF file.
    """
    settings = get_settings()
    out_dir = Path(output_dir or settings.resume_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_company = "".join(c if c.isalnum() else "_" for c in company)[:20]
    filename = f"resume_{safe_company}_{timestamp}_{resume.resume_id[:8]}.pdf"
    pdf_path = str(out_dir / filename)

    styles = _build_styles()
    bullets = resume.get_bullets()

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Resume — {profile.get('name', 'Candidate')} — {role} at {company}",
        author=profile.get("name", "Candidate"),
        subject=f"Application for {role} at {company}",
        creator="Agentic-JobFlow v1.0",
    )

    story = []

    # Header
    story.extend(_build_header(profile, styles))

    # Grounding score watermark (footer note, ATS-invisible via small font)
    story.append(Spacer(1, 4))

    # Tailored experience section
    story.extend(_build_experience_section(company, role, bullets, styles))

    # Skills section (tools extracted from fact types)
    tool_bullets = [b for b in bullets if any(
        fid.startswith("FACT") for fid in b.get("fact_ids", [])
    )]

    # Additional profile sections (if provided)
    if profile.get("education"):
        story.append(Spacer(1, 8))
        story.append(Paragraph("EDUCATION", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        story.append(Spacer(1, 4))
        for edu in profile["education"]:
            story.append(Paragraph(edu.get("institution", ""), styles["job_title"]))
            story.append(Paragraph(edu.get("degree", ""), styles["bullet"]))

    if profile.get("skills"):
        story.append(Spacer(1, 8))
        story.append(Paragraph("SKILLS", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        story.append(Spacer(1, 4))
        story.append(Paragraph(", ".join(profile["skills"]), styles["skills"]))

    doc.build(story)
    return pdf_path
