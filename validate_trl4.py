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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    # ── Batch 2: Stress-test roles (partial match / mismatch) ─────────────────
    {
        "company": "NLP Innovations",
        "role": "NLP Engineer",
        "ats_type": "greenhouse",
        "jd": """
We are looking for an NLP Engineer to join our language AI team.

Requirements:
- Experience with text processing pipelines (tokenization, embedding, similarity)
- Sentence Transformers, HuggingFace Transformers, or similar
- Python, NumPy, Pandas for data manipulation
- Familiarity with LLM prompt engineering and RAG architectures
- FAISS, ChromaDB, or other vector store experience
- Strong understanding of semantic search and information retrieval

Responsibilities:
- Build and maintain NLP pipelines for production use
- Research and implement state-of-the-art embedding models
- Evaluate retrieval quality and optimize chunking strategies
        """,
    },
    {
        "company": "AgentTech Labs",
        "role": "AI Agent Developer",
        "ats_type": "lever",
        "jd": """
Build the next generation of autonomous AI agents.

Requirements:
- Python proficiency for agentic system development
- Experience with LLM APIs (Gemini, OpenAI, Claude)
- Multi-channel bot development (Telegram, Slack, Discord)
- Understanding of tool-use, function calling, and agent orchestration
- REST API development and integration
- Streamlit or similar for rapid prototyping of agent interfaces

Nice to have:
- Experience with LangChain, LlamaIndex, or custom agent frameworks
- Multi-step reasoning and task planning implementations
        """,
    },
    {
        "company": "OpenData Foundation",
        "role": "Data Analyst",
        "ats_type": "ashby",
        "jd": """
Join our open data team as a Data Analyst.

Requirements:
- Python (Pandas, NumPy, Matplotlib, Seaborn) for data analysis
- SQL for data extraction and querying
- Statistical analysis and hypothesis testing
- Experience with EDA and data visualization
- Clear communication of data insights to non-technical stakeholders
- Experience with classification or regression models a plus

Responsibilities:
- Analyze large datasets and produce actionable insights
- Create dashboards and visualization reports
- Support data-driven decision making across teams
        """,
    },
    {
        "company": "MedAI Startup",
        "role": "Junior ML Engineer",
        "ats_type": "greenhouse",
        "jd": """
Early-stage health AI startup hiring a Junior ML Engineer.

Requirements:
- Scikit-Learn for ML model development
- Python, Pandas, NumPy
- Experience with classification tasks on real datasets
- Preprocessing skills: missing value handling, feature scaling, encoding
- Model evaluation: precision, recall, F1, confusion matrices
- Familiarity with train/test splits and cross-validation

We work on medical imaging and patient outcome prediction.
        """,
    },
    {
        "company": "StreamAI",
        "role": "Generative AI Developer",
        "ats_type": "lever",
        "jd": """
We are building generative AI products for enterprise customers.

Requirements:
- Experience with LLM integration (Gemini, GPT, Claude)
- Prompt engineering and output control
- Python backend development
- API design and integration (REST)
- Streamlit or Gradio for demo and prototype interfaces
- Understanding of hallucination mitigation techniques

Bonus:
- RAG pipeline experience
- Multi-modal AI (text + image)
- Experience deploying AI products to real users
        """,
    },
    # ── Batch 3: Partial/mismatch roles (stress test — LLM must resist hallucination) ──
    {
        "company": "FinanceCore",
        "role": "Quantitative Analyst",
        "ats_type": "other",
        "jd": """
Quantitative Analyst for a leading financial services firm.

Requirements:
- Advanced mathematics: stochastic calculus, linear algebra, probability
- Python or R for quantitative modeling
- Experience with options pricing, risk models, or portfolio optimization
- SQL and large financial dataset processing
- Familiarity with Bloomberg or financial data APIs
- 3+ years of experience in a quantitative finance role

We need someone with deep domain expertise in derivatives and risk management.
        """,
    },
    {
        "company": "CloudOps Inc",
        "role": "DevOps / SRE Engineer",
        "ats_type": "greenhouse",
        "jd": """
Senior DevOps/SRE Engineer to manage our cloud infrastructure.

Requirements:
- 4+ years of DevOps/SRE experience
- Kubernetes, Docker, Helm for container orchestration
- Terraform or Ansible for infrastructure-as-code
- CI/CD pipelines: GitHub Actions, Jenkins, ArgoCD
- AWS or GCP expertise (certifications preferred)
- Monitoring: Prometheus, Grafana, Datadog
- On-call experience and incident response

You will own production reliability for systems serving millions of users.
        """,
    },
    {
        "company": "MobileFirst",
        "role": "iOS Developer",
        "ats_type": "ashby",
        "jd": """
iOS Developer to build consumer-facing mobile applications.

Requirements:
- Swift and SwiftUI for iOS development
- Xcode proficiency
- UIKit and Core Data experience
- REST API integration from mobile clients
- App Store submission and TestFlight experience
- 2+ years of professional iOS development

Nice to have: React Native for cross-platform development.
        """,
    },
    {
        "company": "EmbeddedSys",
        "role": "Embedded Systems Engineer",
        "ats_type": "other",
        "jd": """
Embedded Systems Engineer for IoT product development.

Requirements:
- C/C++ for embedded firmware development
- Microcontroller programming (STM32, ESP32, Arduino)
- RTOS experience (FreeRTOS preferred)
- Hardware debugging (oscilloscope, logic analyzer)
- I2C, SPI, UART communication protocols
- PCB design knowledge (KiCad or Altium) a plus

You will work directly with hardware teams to bring IoT products to market.
        """,
    },
    # ── Batch 4: Closely matched senior roles (test for metric hallucination) ───
    {
        "company": "VectorDB Co",
        "role": "Senior ML Engineer - Search",
        "ats_type": "greenhouse",
        "jd": """
Senior ML Engineer to build and scale our vector search infrastructure.

Requirements:
- 3+ years of ML engineering experience
- Vector search systems: FAISS, Annoy, ScaNN
- Embedding models: Sentence Transformers, OpenAI embeddings
- Python, strong software engineering fundamentals
- Experience scaling ML systems to production
- Familiarity with RAG, semantic search, recommendation systems

Bonus: experience with LLM fine-tuning or RLHF.
        """,
    },
    {
        "company": "Blockchain Ventures",
        "role": "Smart Contract Developer",
        "ats_type": "lever",
        "jd": """
Smart Contract Developer to build decentralized finance protocols.

Requirements:
- Solidity for EVM-compatible smart contract development
- Hardhat or Truffle for development and testing
- Web3.js or Ethers.js for dApp integration
- Understanding of DeFi protocols (AMMs, lending, staking)
- Security-first mindset: reentrancy, overflow, access control
- React or Next.js for dApp frontends

Experience with Supabase or similar for off-chain data storage is a plus.
        """,
    },
    {
        "company": "Streamlit Cloud",
        "role": "Developer Advocate / ML Tools",
        "ats_type": "ashby",
        "jd": """
Developer Advocate focused on ML tooling and data apps.

Requirements:
- Python proficiency
- Experience building Streamlit, Gradio, or similar data apps
- Understanding of ML workflows (training, evaluation, deployment)
- Excellent communication skills — writing, demos, tutorials
- Experience with LLM APIs and AI application development
- GitHub for open-source contributions and community engagement

You will create tutorials, demos, and example apps to help developers build faster.
        """,
    },
    {
        "company": "InsureTech",
        "role": "Data Scientist - Predictive Modeling",
        "ats_type": "greenhouse",
        "jd": """
Data Scientist to build predictive models for insurance risk assessment.

Requirements:
- Scikit-Learn, XGBoost, LightGBM for model development
- Python, Pandas, NumPy for data manipulation
- Statistical modeling: GLM, survival analysis, Bayesian methods
- Model evaluation and calibration
- SQL for data extraction
- Strong EDA skills and domain curiosity

You will build models that directly impact underwriting decisions.
        """,
    },
    {
        "company": "EdTech Platform",
        "role": "Full Stack Developer",
        "ats_type": "lever",
        "jd": """
Full Stack Developer to build our next-generation learning platform.

Requirements:
- React or Next.js for frontend
- Python or Node.js backend
- Supabase or Firebase for backend services
- REST API design and integration
- SQL and basic database design
- Authentication and role-based access control

Nice to have:
- AI integration (LLM-powered tutoring or content generation)
- Real-time features (websockets, live collaboration)
        """,
    },
    {
        "company": "OpenSource AI",
        "role": "ML Research Engineer (Intern/Junior)",
        "ats_type": "other",
        "jd": """
Research-oriented ML Engineer to work on open-source AI tooling.

Requirements:
- Python, strong fundamentals in ML
- Scikit-Learn, PyTorch or TensorFlow for model development
- Experience with model evaluation pipelines
- GitHub for open-source collaboration
- Written communication for documentation and blog posts
- Curiosity about AI safety, interpretability, or alignment (preferred)

This is a research-adjacent role — you'll build tools used by ML researchers worldwide.
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

    if settings.llm_provider == "groq" and not settings.groq_api_key:
        print("\nERROR: GROQ_API_KEY not set in .env")
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
