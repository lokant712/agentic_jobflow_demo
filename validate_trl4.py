"""
TRL-4 Validation Experiment
============================
Runs the full Tailor Agent -> Grounding Verifier pipeline against real
job descriptions using the configured LLM provider (Gemini).

Outputs:
  - Per-JD grounding scores
  - Overall mean / min / distribution
  - Drop rate (bullets killed by verifier)
  - Writes: data/logs/trl4_validation_report.json

Usage:
    python validate_trl4.py

Before running:
    1. Fill in GEMINI_API_KEY in .env
    2. Replace RESUME_TEXT below with your actual resume text
    3. Optionally add more JDs to JOB_DESCRIPTIONS list
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make sure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from backend.app.config import get_settings
from backend.app.services.llm_client import get_llm_client
from backend.app.services.tailoring.grounding_verifier import (
    extract_key_entities,
    check_entity_grounded,
)


# ==============================================================================
# EDIT THIS SECTION -- your real resume and job descriptions
# ==============================================================================

RESUME_TEXT = """
[PASTE YOUR RESUME TEXT HERE]

Example format:
John Doe | john@email.com | +91-XXXXXXXXXX

EXPERIENCE
Senior Software Engineer - Acme Corp (2021-2024)
- Reduced API latency by 40% through Redis caching and query optimization
- Led a team of 5 engineers to deliver a microservices migration on AWS
- Built a real-time analytics pipeline using Kafka and Apache Spark processing 2M events/day

Software Engineer - Beta Systems (2019-2021)
- Developed REST APIs using Python FastAPI serving 50,000 daily active users
- Improved test coverage from 45% to 92% using pytest and CI/CD on GitHub Actions
- Reduced infrastructure costs by $120K/year by migrating to Kubernetes

SKILLS
Python, FastAPI, AWS, Kubernetes, Docker, PostgreSQL, Redis, Kafka, Apache Spark, React

EDUCATION
B.Tech Computer Science - IIT Bombay (2015-2019), GPA: 8.7/10
"""

JOB_DESCRIPTIONS = [
    {
        "company": "DataFlow Inc",
        "role": "Senior Backend Engineer",
        "ats_type": "greenhouse",
        "jd": """
We are looking for a Senior Backend Engineer to join our platform team.

Requirements:
- 5+ years of backend development experience
- Strong Python skills, experience with FastAPI or Django
- Experience with AWS or GCP cloud infrastructure
- Experience with data pipelines and streaming systems (Kafka, Spark a plus)
- PostgreSQL or similar relational databases
- Docker and Kubernetes for containerized deployments

Responsibilities:
- Design and implement scalable REST APIs handling millions of requests
- Lead technical architecture decisions for platform services
- Mentor junior engineers and conduct code reviews
        """,
    },
    {
        "company": "CloudScale Solutions",
        "role": "Platform Engineer",
        "ats_type": "lever",
        "jd": """
CloudScale is hiring a Platform Engineer to help build our infrastructure.

Requirements:
- Experience with Kubernetes and container orchestration
- Infrastructure as Code (Terraform, Ansible)
- CI/CD pipeline setup and maintenance
- AWS/GCP/Azure cloud services
- Python or Go for automation scripting

We value candidates who have experience reducing cloud costs and improving reliability.
        """,
    },
    {
        "company": "StreamAnalytics",
        "role": "Data Engineer",
        "ats_type": "ashby",
        "jd": """
We're building the next generation of real-time data infrastructure.

Requirements:
- 3+ years of data engineering experience
- Apache Kafka, Apache Spark, or similar streaming technologies
- Python and SQL proficiency
- ETL pipeline design and optimization
- Experience with high-volume data processing (millions of events)
        """,
    },
    {
        "company": "FinTech Startup",
        "role": "Full Stack Engineer",
        "ats_type": "greenhouse",
        "jd": """
Join our fintech startup as a Full Stack Engineer.

Requirements:
- React or Vue.js for frontend development
- Python or Node.js for backend
- REST API design and development
- PostgreSQL or MongoDB
- Agile/Scrum methodology
        """,
    },
    {
        "company": "AI Research Lab",
        "role": "ML Infrastructure Engineer",
        "ats_type": "other",
        "jd": """
We are looking for an ML Infrastructure Engineer to support our research team.

Requirements:
- Experience with ML model serving infrastructure
- Python, PyTorch or TensorFlow
- Kubernetes for ML workloads
- Strong software engineering fundamentals

You will bridge the gap between research and production ML systems.
        """,
    },
]

# ==============================================================================
# Validation Engine
# ==============================================================================

def segment_resume(text: str) -> list[dict]:
    """Extract fact units from resume text (mirrors profile_service logic)."""
    import re
    facts = []
    counter = [0]

    def make_fact(type_: str, line: str) -> dict:
        counter[0] += 1
        return {"fact_id": f"FACT-{counter[0]:03d}", "type": type_, "text": line.strip()}

    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 15:
            continue
        if line.isupper() or line.endswith(":"):
            continue

        if re.search(r"\d+%|\$[\d,]+|[\d,]+[kKmM]?\s*(users|events|requests|day|year)", line, re.I):
            facts.append(make_fact("metric", line))
        elif re.search(r"\b(python|java|aws|gcp|kubernetes|docker|kafka|spark|react|fastapi|postgres|redis|terraform)\b", line, re.I):
            facts.append(make_fact("tool", line))
        elif re.search(r"\b(led|built|designed|delivered|improved|reduced|increased|migrated|developed)\b", line, re.I):
            facts.append(make_fact("responsibility", line))
        elif re.search(r"\b(B\.Tech|M\.Tech|PhD|Bachelor|Master|GPA|University|IIT)\b", line, re.I):
            facts.append(make_fact("education", line))
        else:
            facts.append(make_fact("responsibility", line))

    return facts


def verify_bullets_offline(bullets: list[dict], facts: list[dict]) -> list[dict]:
    """Run the real grounding verifier logic (no DB needed)."""
    results = []
    for bullet in bullets:
        text = bullet.get("text", "")
        fact_ids = bullet.get("fact_ids", [])

        cited_texts = [f["text"] for f in facts if f["fact_id"] in fact_ids]
        if not cited_texts:
            results.append({
                "bullet": text, "fact_ids": fact_ids, "passed": False,
                "reason": "cited_fact_ids_not_found", "failed_entities": fact_ids,
                "entities_checked": [],
            })
            continue

        entities = extract_key_entities(text)
        if not entities:
            results.append({
                "bullet": text, "fact_ids": fact_ids, "passed": True,
                "reason": "no_key_entities_pass", "failed_entities": [],
                "entities_checked": [],
            })
            continue

        failed = [e for e in entities if not check_entity_grounded(e, cited_texts)]
        results.append({
            "bullet": text, "fact_ids": fact_ids,
            "passed": len(failed) == 0,
            "reason": "all_grounded" if not failed else f"entities_not_in_facts:{failed}",
            "failed_entities": failed,
            "entities_checked": entities,
        })

    return results


async def run_single_jd(client, facts: list[dict], job: dict, idx: int) -> dict:
    """Tailor + verify for one job description."""
    valid_fact_ids = {f["fact_id"] for f in facts}
    facts_json = json.dumps(facts, indent=2)

    system_prompt = (
        "You are a resume tailoring assistant. Write tailored resume bullet points "
        "using ONLY the provided facts. STRICT RULES:\n"
        "1. Every bullet MUST cite at least one fact_id from the provided facts.\n"
        "2. Do NOT invent, embellish, or add any information not in the cited facts.\n"
        "3. Do NOT combine metrics from different facts.\n"
        "4. Output ONLY valid JSON: [{\"text\": \"...\", \"fact_ids\": [\"FACT-001\"]}, ...]\n"
        "5. Maximum 8 bullets. Action-verb first."
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"JOB: {job['company']} - {job['role']}\n"
        f"JD:\n{job['jd'][:2000]}\n\n"
        f"CANDIDATE FACTS:\nFACTS_JSON: {facts_json}\n\n"
        f"Generate tailored bullets now (JSON only):"
    )

    total = len(JOB_DESCRIPTIONS)
    print(f"\n[{idx+1}/{total}] Tailoring for: {job['company']} / {job['role']}")

    try:
        raw_bullets = await client.complete_json(prompt)
    except Exception as e:
        print(f"  ERROR: LLM call failed: {e}")
        return {"company": job["company"], "role": job["role"], "error": str(e)}

    if not isinstance(raw_bullets, list):
        print(f"  ERROR: LLM returned non-list: {type(raw_bullets)}")
        return {"company": job["company"], "role": job["role"], "error": "non_list_response"}

    # Structural validation
    valid_bullets = []
    for b in raw_bullets:
        if not isinstance(b, dict):
            continue
        text = (b.get("text") or "").strip()
        fids = [fid for fid in (b.get("fact_ids") or []) if fid in valid_fact_ids]
        if text and fids:
            valid_bullets.append({"text": text, "fact_ids": fids})

    print(f"  Raw: {len(raw_bullets)} bullets  |  Structurally valid: {len(valid_bullets)}")

    # Grounding verification
    vresults = verify_bullets_offline(valid_bullets, facts)
    passed = [r for r in vresults if r["passed"]]
    dropped = [r for r in vresults if not r["passed"]]
    score = round(len(passed) / len(valid_bullets), 4) if valid_bullets else 0.0

    flag = "PASS" if score >= 0.95 else "WARN" if score >= 0.80 else "FAIL"
    print(f"  Grounding Score: {score:.1%} [{flag}]  ({len(passed)} pass, {len(dropped)} drop)")
    for d in dropped:
        print(f"    DROPPED: '{d['bullet'][:70]}'")
        print(f"    Reason:  {d['reason']}")

    return {
        "company": job["company"],
        "role": job["role"],
        "ats_type": job["ats_type"],
        "raw_bullets": len(raw_bullets),
        "structurally_valid": len(valid_bullets),
        "grounding_score": score,
        "passed_bullets": len(passed),
        "dropped_bullets": len(dropped),
        "bullet_detail": vresults,
    }


async def main():
    settings = get_settings()

    print("=" * 60)
    print("  Agentic-JobFlow  |  TRL-4 Validation Experiment")
    print("=" * 60)
    print(f"  Provider : {settings.llm_provider}")
    print(f"  Model    : {settings.llm_model}")
    print(f"  JDs      : {len(JOB_DESCRIPTIONS)}")
    print("=" * 60)

    if settings.llm_provider == "gemini" and not settings.gemini_api_key:
        print("\nERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    if "[PASTE YOUR RESUME TEXT HERE]" in RESUME_TEXT:
        print("\nERROR: Replace RESUME_TEXT with your actual resume in validate_trl4.py")
        sys.exit(1)

    facts = segment_resume(RESUME_TEXT)
    print(f"\nParsed {len(facts)} fact units from resume:")
    for f in facts:
        print(f"  [{f['fact_id']}] ({f['type']:14s}) {f['text'][:65]}")

    client = get_llm_client(provider=settings.llm_provider, model=settings.llm_model)

    results = []
    for i, job in enumerate(JOB_DESCRIPTIONS):
        result = await run_single_jd(client, facts, job, i)
        results.append(result)
        if i < len(JOB_DESCRIPTIONS) - 1:
            await asyncio.sleep(1)  # avoid rate limiting

    # Summary
    successful = [r for r in results if "error" not in r]
    scores = [r["grounding_score"] for r in successful]

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  JDs processed        : {len(successful)}/{len(results)}")

    if scores:
        mean_score = sum(scores) / len(scores)
        print(f"  Mean Grounding Score : {mean_score:.1%}")
        print(f"  Min / Max            : {min(scores):.1%} / {max(scores):.1%}")
        print(f"  >= 0.95 threshold    : {sum(1 for s in scores if s >= 0.95)}/{len(scores)} JDs")

        total_bullets = sum(r.get("structurally_valid", 0) for r in successful)
        total_dropped = sum(r.get("dropped_bullets", 0) for r in successful)
        drop_rate = total_dropped / total_bullets if total_bullets else 0
        print(f"  Total bullets        : {total_bullets}")
        print(f"  Dropped by verifier  : {total_dropped} ({drop_rate:.1%} drop rate)")

        print("\n  Per-JD breakdown:")
        for r in successful:
            flag = "OK" if r["grounding_score"] >= 0.95 else "!!" if r["grounding_score"] >= 0.80 else "XX"
            print(f"  [{flag}] {r['company']:25s} | {r['role']:28s} | {r['grounding_score']:.1%}")

    # Write report
    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "num_facts": len(facts),
        "num_jds": len(JOB_DESCRIPTIONS),
        "scores": scores,
        "mean_grounding_score": round(sum(scores) / len(scores), 4) if scores else 0,
        "min_grounding_score": round(min(scores), 4) if scores else 0,
        "max_grounding_score": round(max(scores), 4) if scores else 0,
        "results": results,
    }

    out_path = Path("data/logs")
    out_path.mkdir(parents=True, exist_ok=True)
    report_path = out_path / "trl4_validation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\nFull report -> {report_path}")
    print("Use mean grounding score + drop rate as your TRL-4 evidence (IDF Section 8).")


if __name__ == "__main__":
    asyncio.run(main())
