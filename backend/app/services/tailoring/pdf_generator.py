"""
PDF Generator — FR-5.3

Generates professional, Executive / Ivy-League standard ATS-compatible PDF resumes
from verified TailoredResume bullets.
Uses ReportLab with high-fidelity formatting.

ATS safety & aesthetic rules enforced:
  - Standard, clean typography (Helvetica & Helvetica-Bold)
  - Single-column linear layout (100% parseable by Greenhouse, Lever, Ashby, Workday)
  - Section headers with clean dividing rules
  - Structured sections: Header, Education, Technical Skills, Experience & Projects
  - Machine-readable vector text layer
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    HRFlowable,
    Table,
    TableStyle,
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
        fontSize=17,
        leading=21,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=3,
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=6,
        textColor=colors.HexColor("#334155"),
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        spaceBefore=8,
        spaceAfter=2,
        textColor=colors.HexColor("#0f172a"),
        textTransform="uppercase",
    )
    item_title_style = ParagraphStyle(
        "ItemTitle",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#0f172a"),
    )
    item_subtitle_style = ParagraphStyle(
        "ItemSubtitle",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
    )
    date_style = ParagraphStyle(
        "Date",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#64748b"),
    )
    bullet_style = ParagraphStyle(
        "BulletItem",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        leftIndent=12,
        spaceAfter=2,
        textColor=colors.HexColor("#1e293b"),
    )
    skills_label_style = ParagraphStyle(
        "SkillsLabel",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#0f172a"),
    )
    skills_text_style = ParagraphStyle(
        "SkillsText",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#334155"),
    )

    return {
        "name": name_style,
        "contact": contact_style,
        "section_header": section_header_style,
        "item_title": item_title_style,
        "item_subtitle": item_subtitle_style,
        "date": date_style,
        "bullet": bullet_style,
        "skills_label": skills_label_style,
        "skills_text": skills_text_style,
    }


# ─── 1. Header Section ────────────────────────────────────────────────────────

def _build_header(profile: dict, styles: dict) -> list:
    elements = []
    name = profile.get("name") or f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or "Lokanth Srihari"
    elements.append(Paragraph(name, styles["name"]))

    contact_parts = []
    if profile.get("email"):
        contact_parts.append(f"<a href='mailto:{profile['email']}' color='#2563eb'>{profile['email']}</a>")
    if profile.get("phone"):
        contact_parts.append(str(profile["phone"]))
    if profile.get("location"):
        contact_parts.append(str(profile["location"]))
    if profile.get("linkedin"):
        contact_parts.append("<a href='https://linkedin.com/in/lokanth' color='#2563eb'>LinkedIn</a>")
    if profile.get("github"):
        contact_parts.append("<a href='https://github.com/lokant712' color='#2563eb'>GitHub</a>")

    if contact_parts:
        elements.append(Paragraph(" • ".join(contact_parts), styles["contact"]))

    elements.append(HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#0f172a"), spaceAfter=4))
    return elements


# ─── 2. Education Section ─────────────────────────────────────────────────────

def _build_education_section(styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Education", styles["section_header"]))
    elements.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#94a3b8"), spaceAfter=3))

    table_data = [
        [
            Paragraph("<b>Vellore Institute of Technology (VIT)</b> — <i>Integrated M.Tech in Computer Science & Engineering (Data Science)</i>", styles["skills_text"]),
            Paragraph("2021 – 2026", styles["date"]),
        ]
    ]
    t = Table(table_data, colWidths=[420, 100])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 4))
    return elements


# ─── 3. Technical Skills Section ──────────────────────────────────────────────

def _build_skills_section(styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Technical Skills", styles["section_header"]))
    elements.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#94a3b8"), spaceAfter=3))

    skills = [
        ("Languages & Core:", "Python, C++, SQL, Bash, Data Structures & Algorithms"),
        ("AI & Machine Learning:", "Retrieval-Augmented Generation (RAG), PyTorch, Scikit-Learn, Sentence-Transformers, HuggingFace"),
        ("Vector Search & Databases:", "Qdrant, FAISS, BM25, PostgreSQL, SQLite, Vector Indexing & Semantic Search"),
        ("Backend & Developer Tools:", "FastAPI, Streamlit, Asynchronous Programming, Git, GitHub, Docker, Playwright, Linux"),
    ]

    for label, text in skills:
        p = Paragraph(f"<b>{label}</b> {text}", styles["skills_text"])
        elements.append(p)
        elements.append(Spacer(1, 1))

    elements.append(Spacer(1, 4))
    return elements


# ─── 4. Professional Experience & Projects Section ─────────────────────────────

def _build_experience_section(
    company: str,
    role: str,
    bullets: list[dict],
    styles: dict,
) -> list:
    elements = []
    elements.append(Paragraph("Experience & Key Projects", styles["section_header"]))
    elements.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#94a3b8"), spaceAfter=3))

    # Project / Experience Header Table
    exp_table_data = [
        [
            Paragraph(f"<b>{role}</b> — <i>Customer Intelligence & RAG Platform ({company})</i>", styles["item_title"]),
            Paragraph("Present", styles["date"]),
        ]
    ]
    t = Table(exp_table_data, colWidths=[420, 100])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 2))

    # Verified Fact-ID Grounded Bullets
    for bullet in bullets:
        text = bullet.get("text", "").strip()
        if text:
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
    FR-5.3: Generate a pristine, single-page Executive ATS-compatible PDF resume.
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

    # 0.5 inch (36pt) margins for clean 1-page geometry
    margin = 36
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    story = []
    story.extend(_build_header(profile, styles))
    story.extend(_build_education_section(styles))
    story.extend(_build_skills_section(styles))
    story.extend(_build_experience_section(company, role, bullets, styles))

    doc.build(story)
    return pdf_path
