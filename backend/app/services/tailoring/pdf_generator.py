"""
PDF Generator — FR-5.3

Generates exact, pixel-perfect, ATS-compatible resumes matching the user's reference PDF format:
- Centered Header: Name (Bold uppercase), contact bar with clickable links
- Underlined Section Headers: EDUCATION, PROJECTS & EXPERIENCE, SKILLS, INTERESTS
- Precise 2-column tables for Institute/Project and Location/Date
- Indented circular bullet points
- Fits perfectly on 1 standard Letter page with 36pt (0.5 in) margins
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


def _build_styles():
    base = getSampleStyleSheet()

    name_style = ParagraphStyle(
        "CandidateName",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        alignment=TA_CENTER,
        textColor=colors.black,
        spaceAfter=2,
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=6,
        textColor=colors.black,
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        spaceBefore=5,
        spaceAfter=1,
        textColor=colors.black,
    )
    item_title_bold = ParagraphStyle(
        "ItemTitleBold",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.black,
    )
    item_subtitle_italic = ParagraphStyle(
        "ItemSubtitleItalic",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=11,
        textColor=colors.black,
    )
    right_align_bold = ParagraphStyle(
        "RightAlignBold",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=TA_RIGHT,
        textColor=colors.black,
    )
    right_align_italic = ParagraphStyle(
        "RightAlignItalic",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=11,
        alignment=TA_RIGHT,
        textColor=colors.black,
    )
    body_text = ParagraphStyle(
        "BodyText",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.black,
    )
    bullet_style = ParagraphStyle(
        "BulletItem",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        leftIndent=10,
        spaceAfter=1.5,
        textColor=colors.black,
    )

    return {
        "name": name_style,
        "contact": contact_style,
        "section_header": section_header_style,
        "item_title_bold": item_title_bold,
        "item_subtitle_italic": item_subtitle_italic,
        "right_bold": right_align_bold,
        "right_italic": right_align_italic,
        "body": body_text,
        "bullet": bullet_style,
    }


def _make_table_row(left_flowable, right_flowable):
    t = Table([[left_flowable, right_flowable]], colWidths=[380, 160])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.5),
        ('TOPPADDING', (0, 0), (-1, -1), 0.5),
    ]))
    return t


def _add_section_header(title: str, styles: dict) -> list:
    return [
        Paragraph(title, styles["section_header"]),
        HRFlowable(width="100%", thickness=0.75, color=colors.black, spaceBefore=1, spaceAfter=3),
    ]


def _build_header(profile: dict, styles: dict) -> list:
    name = profile.get("name") or "LOKANTH SRIHARI"
    elements = [Paragraph(name.upper(), styles["name"])]

    email = profile.get("email", "lokanth2006@gmail.com")
    phone = profile.get("phone", "8838379971")
    linkedin = profile.get("linkedin", "https://linkedin.com/in/lokanth")
    github = profile.get("github", "https://github.com/lokant712")

    contact_html = (
        f"<a href='mailto:{email}' color='#0000ee'>{email}</a> | {phone} | "
        f"<a href='{linkedin}' color='#0000ee'>Linkedin</a> | "
        f"<a href='{github}' color='#0000ee'>GitHub</a>"
    )
    elements.append(Paragraph(contact_html, styles["contact"]))
    return elements


def _build_education_section(styles: dict) -> list:
    elements = _add_section_header("EDUCATION", styles)

    row1 = _make_table_row(
        Paragraph("<b>Vellore Institute of Technology</b>", styles["item_title_bold"]),
        Paragraph("<b>Vellore, Tamil Nadu</b>", styles["right_bold"]),
    )
    row2 = _make_table_row(
        Paragraph("<i>Integrated M.Tech in Computer Science Engineering (Data Science)</i>", styles["item_subtitle_italic"]),
        Paragraph("<i>Expected Graduation, May 2028</i>", styles["right_italic"]),
    )
    elements.extend([row1, row2])

    edu_bullets = [
        "Concentrations: Data Science & Artificial Intelligence",
        "CGPA: 8.44/10",
        "Related Coursework: Data Structures & Algorithms, Objects & Design, Computer Organization & Programming, Combinatorics, Machine Learning, Artificial Intelligence, Object-Oriented Programming, Statistics & Applications",
    ]
    for b in edu_bullets:
        elements.append(Paragraph(f"&nbsp;&nbsp;◦&nbsp;&nbsp;{b}", styles["bullet"]))

    elements.append(Spacer(1, 4))
    return elements


def _build_projects_experience(
    tailored_bullets: list[dict],
    styles: dict,
) -> list:
    elements = _add_section_header("PROJECTS & EXPERIENCE", styles)

    # 1. Customer Intelligence RAG System (Target project tailored to the JD)
    p1_row1 = _make_table_row(
        Paragraph("<b>Customer Intelligence RAG System</b>", styles["item_title_bold"]),
        Paragraph("<b>Vellore, Tamil Nadu</b>", styles["right_bold"]),
    )
    p1_row2 = _make_table_row(
        Paragraph("<i>Machine Learning & Backend Developer</i>", styles["item_subtitle_italic"]),
        Paragraph("<i>Jan 2026 – Feb 2026</i>", styles["right_italic"]),
    )
    elements.extend([p1_row1, p1_row2])

    if tailored_bullets:
        for b in tailored_bullets[:5]:
            text = b.get("text", "").strip()
            if text:
                elements.append(Paragraph(f"•&nbsp;&nbsp;{text}", styles["bullet"]))
    else:
        elements.append(Paragraph("•&nbsp;&nbsp;Built a Retrieval-Augmented Generation (RAG) system to enable natural-language querying over customer feedback data.", styles["bullet"]))
        elements.append(Paragraph("•&nbsp;&nbsp;Implemented semantic search using Sentence Transformers (all-MiniLM-L6-v2) and FAISS for top-k vector similarity retrieval.", styles["bullet"]))
        elements.append(Paragraph("•&nbsp;&nbsp;Integrated Google Gemini 2.5 Flash for context-grounded answer generation with prompt constraints to reduce hallucination.", styles["bullet"]))
        elements.append(Paragraph("•&nbsp;&nbsp;Developed a Streamlit web interface and an asynchronous Telegram bot for multi-channel access.", styles["bullet"]))
        elements.append(Paragraph("•&nbsp;&nbsp;Designed a modular pipeline (ingestion, indexing, retrieval, generation) for scalable and efficient querying.", styles["bullet"]))

    elements.append(Spacer(1, 3))

    # 2. BloodLink
    p2_row1 = _make_table_row(
        Paragraph("<b>BloodLink</b>", styles["item_title_bold"]),
        Paragraph("<b>Vellore, Tamil Nadu</b>", styles["right_bold"]),
    )
    p2_row2 = _make_table_row(
        Paragraph("<i>Full Stack & Blockchain Developer</i>", styles["item_subtitle_italic"]),
        Paragraph("<i>Aug 2025 – Oct 2025</i>", styles["right_italic"]),
    )
    elements.extend([p2_row1, p2_row2])
    elements.append(Paragraph("•&nbsp;&nbsp;Built a full-stack blood donation platform using React, Tailwind, Supabase, and Edge Functions.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Developed Solidity smart contracts for blockchain-based donor certificate verification.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Integrated Google Gemini AI to create a real-time medical assistance chatbot.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Implemented role-based dashboards and emergency request workflows.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Implemented secure backend–frontend communication using Supabase Edge Functions.", styles["bullet"]))

    elements.append(Spacer(1, 3))

    # 3. Donor Health Classification
    p3_row1 = _make_table_row(
        Paragraph("<b>Donor Health Classification</b>", styles["item_title_bold"]),
        Paragraph("<b>Vellore, Tamil Nadu</b>", styles["right_bold"]),
    )
    p3_row2 = _make_table_row(
        Paragraph("<i>Machine Learning Engineer</i>", styles["item_subtitle_italic"]),
        Paragraph("<i>Oct 2025 – Nov 2025</i>", styles["right_italic"]),
    )
    elements.extend([p3_row1, p3_row2])
    elements.append(Paragraph("•&nbsp;&nbsp;Developed an SVM classifier achieving 91.5% accuracy on biochemical donor data.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Implemented preprocessing steps: imputation, label encoding, feature scaling, and outlier handling.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Performed GridSearchCV hyperparameter tuning and built evaluation modules (confusion matrix, F1-scores).", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Conducted EDA using heatmaps, pairplots, and distribution analysis.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Applied stratified sampling to preserve class distribution across training and testing sets.", styles["bullet"]))

    elements.append(Spacer(1, 3))

    # 4. Automated Irrigation System
    p4_row1 = _make_table_row(
        Paragraph("<b>Automated Irrigation System</b>", styles["item_title_bold"]),
        Paragraph("<b>Vellore, Tamil Nadu</b>", styles["right_bold"]),
    )
    p4_row2 = _make_table_row(
        Paragraph("<i>Software Developer</i>", styles["item_subtitle_italic"]),
        Paragraph("<i>Mar 2025 – April 2025</i>", styles["right_italic"]),
    )
    elements.extend([p4_row1, p4_row2])
    elements.append(Paragraph("•&nbsp;&nbsp;Implemented CSV-based sensor data processing for moisture, temperature, humidity, pH, and sunlight.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Built threshold-based condition analysis and irrigation decision logic in C.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Integrated modules for reading input, evaluating conditions, making decisions, and writing output.", styles["bullet"]))
    elements.append(Paragraph("•&nbsp;&nbsp;Contributed to system design, UML diagrams, and architecture documentation.", styles["bullet"]))

    elements.append(Spacer(1, 4))
    return elements


def _build_skills_interests_section(styles: dict) -> list:
    elements = []
    elements.extend(_add_section_header("SKILLS", styles))

    skills_lines = [
        "<b>Programming:</b> Python, Java, JavaScript, C/C++, SQL",
        "<b>ML & Data:</b> Scikit-Learn, NumPy, Pandas, Matplotlib, Seaborn",
        "<b>Tools & Platforms:</b> GitHub, Supabase, Cursor, Antigravity, AWS (Basics)",
    ]
    for line in skills_lines:
        elements.append(Paragraph(line, styles["body"]))
        elements.append(Spacer(1, 1))

    elements.append(Spacer(1, 3))
    elements.extend(_add_section_header("INTERESTS", styles))
    elements.append(
        Paragraph(
            "Artificial Intelligence, Generative AI, Full-Stack Development, Machine Learning, Cloud Architecture, AI Applications.",
            styles["body"],
        )
    )
    return elements


def generate_resume_pdf(
    resume: TailoredResume,
    profile: dict,
    company: str,
    role: str,
    output_dir: str | None = None,
) -> str:
    """
    FR-5.3: Generate exact 1-page resume PDF matching user's official format.
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

    # 36pt (0.5 inch) margins
    margin = 36
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=28,
        bottomMargin=28,
    )

    story = []
    story.extend(_build_header(profile, styles))
    story.extend(_build_education_section(styles))
    story.extend(_build_projects_experience(bullets, styles))
    story.extend(_build_skills_interests_section(styles))

    doc.build(story)
    return pdf_path
