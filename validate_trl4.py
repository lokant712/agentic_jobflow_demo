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
LOKANTH SRIHARI
lokanth2006@gmail.com | 8838379971 | LinkedIn | GitHub

EDUCATION
Integrated M.Tech in Computer Science Engineering (Data Science) - Vellore Institute of Technology, Vellore, Tamil Nadu (Expected May 2028), CGPA: 8.44/10

* Concentrations: Data Science & Artificial Intelligence
* Related Coursework: Data Structures & Algorithms, Objects & Design, Computer Organization & Programming, Combinatorics, Machine Learning, Artificial Intelligence, Object-Oriented Programming, Statistics & Applications

PROJECTS & EXPERIENCE
Machine Learning & Backend Developer - Customer Intelligence RAG System (Jan 2026 - Feb 2026)

* Built a Retrieval-Augmented Generation (RAG) system to enable natural-language querying over customer feedback data
* Implemented semantic search using Sentence Transformers (all-MiniLM-L6-v2) and FAISS for top-k vector similarity retrieval
* Integrated Google Gemini 2.5 Flash for context-grounded answer generation with prompt constraints to reduce hallucination
* Developed a Streamlit web interface and an asynchronous Telegram bot for multi-channel access
* Designed a modular pipeline covering ingestion, indexing, retrieval, and generation for scalable querying

Full Stack & Blockchain Developer - BloodLink (Aug 2025 - Oct 2025)

* Built a full-stack blood donation platform using React, Tailwind, Supabase, and Edge Functions
* Developed Solidity smart contracts for blockchain-based donor certificate verification
* Integrated Google Gemini AI to create a real-time medical assistance chatbot
* Implemented role-based dashboards and emergency request workflows
* Implemented secure backend-front-end communication using Supabase Edge Functions

Machine Learning Engineer - Donor Health Classification (Oct 2025 - Nov 2025)

* Developed an SVM classifier achieving 91.5% accuracy on biochemical donor data
* Implemented preprocessing steps including imputation, label encoding, feature scaling, and outlier handling
* Performed GridSearchCV hyperparameter tuning and built evaluation modules using confusion matrices and F1-scores
* Conducted EDA using heatmaps, pairplots, and distribution analysis
* Applied stratified sampling to preserve class distribution across training and testing sets

Software Developer - Automated Irrigation System (Mar 2025 - Apr 2025)

* Implemented CSV-based sensor data processing for moisture, temperature, humidity, pH, and sunlight
* Built threshold-based condition analysis and irrigation decision logic in C
* Integrated modules for reading input, evaluating conditions, making decisions, and writing output
* Contributed to system design, UML diagrams, and architecture documentation

SKILLS
Python, Java, JavaScript, C/C++, SQL, Scikit-Learn, NumPy, Pandas, Matplotlib, Seaborn, GitHub, Supabase, Cursor, Antigravity, AWS

INTERESTS
Artificial Intelligence, Generative AI, Full-Stack Development, Machine Learning, Cloud Architecture, AI Applications
"""


JOB_DESCRIPTIONS = [
    {
        "company": "Qdrant AI",
        "role": "ML Engineer - RAG Systems",
        "ats_type": "greenhouse",
        "jd": """
We are hiring an ML Engineer focused on Retrieval-Augmented Generation systems.

Requirements:
- Experience building RAG pipelines (embedding, retrieval, generation)
- Python proficiency; experience with LLM APIs (OpenAI, Gemini, Anthropic)
- Vector databases: FAISS, Qdrant, Pinecone, or Chroma
- Sentence Transformers or similar embedding models
- Experience with NLP pipelines and semantic search
- Strong understanding of prompt engineering and hallucination mitigation

Nice to have:
- Streamlit or Gradio for demo interfaces
- Experience with chatbot or conversational AI systems
        """,
    },
    {
        "company": "HealthTech Startup",
        "role": "Machine Learning Engineer",
        "ats_type": "lever",
        "jd": """
Join our health-tech team building ML models for clinical data.

Requirements:
- Scikit-Learn, XGBoost, or similar ML frameworks
- Experience with classification problems on tabular/biomedical data
- Python, Pandas, NumPy for data processing
- Model evaluation: confusion matrices, F1-score, ROC-AUC
- Data preprocessing: imputation, feature scaling, handling class imbalance
- Experience with cross-validation and hyperparameter tuning

Responsibilities:
- Train and evaluate models on health/clinical datasets
- Build preprocessing and feature engineering pipelines
- Document experiments and present findings to clinical stakeholders
        """,
    },
    {
        "company": "Chainvault",
        "role": "Full Stack Web3 Developer",
        "ats_type": "ashby",
        "jd": """
We are building decentralized applications and need a Full Stack Web3 Developer.

Requirements:
- React or Next.js for frontend development
- Solidity for smart contract development
- Web3.js or Ethers.js for blockchain interaction
- Backend API development (Node.js or Python)
- Experience with blockchain-based verification or certificate systems
- Supabase, Firebase, or similar BaaS platforms

Responsibilities:
- Build user-facing dApps with wallet integration
- Write and audit Solidity smart contracts
- Integrate blockchain verification into web applications
        """,
    },
    {
        "company": "Conversational AI Corp",
        "role": "AI Application Developer",
        "ats_type": "greenhouse",
        "jd": """
We're building AI-powered conversational products and need a developer who can
bridge ML research and production applications.

Requirements:
- Python for backend development
- Experience with LLM APIs (Gemini, GPT, Claude)
- Building chatbots or AI assistants (Telegram, Slack, web)
- Prompt engineering and context management
- REST API development
- Streamlit or similar for rapid prototyping

Nice to have:
- Experience with multi-channel AI systems (web + mobile + messaging)
- RAG or knowledge-base integration
        """,
    },
    {
        "company": "DataViz Labs",
        "role": "Data Science Intern / Junior DS",
        "ats_type": "other",
        "jd": """
We're looking for a data science intern or junior data scientist to join our analytics team.

Requirements:
- Python, Pandas, NumPy, Matplotlib, Seaborn
- Machine learning fundamentals: classification, regression, clustering
- Exploratory Data Analysis (EDA) skills
- Statistical knowledge: distributions, sampling, hypothesis testing
- SQL for data querying
- Strong communication skills to present findings

Projects you'll work on:
- Customer segmentation and behavioral analysis
- Predictive model development for business metrics
- Dashboard and visualization creation
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

    # Retry up to 3 times for transient errors (503, rate limit)
    raw_bullets = None
    last_error = None
    for attempt in range(3):
        try:
            raw_bullets = await client.complete_json(prompt)
            break
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str or "quota" in err_str.lower():
                wait = 10 * (2 ** attempt)  # 10s, 20s, 40s
                print(f"  Transient error (attempt {attempt+1}/3), retrying in {wait}s: {e}")
                await asyncio.sleep(wait)
            else:
                break  # Non-transient error, don't retry

    if raw_bullets is None:
        print(f"  ERROR: LLM call failed after retries: {last_error}")
        return {"company": job["company"], "role": job["role"], "error": str(last_error)}

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
