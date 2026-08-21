"""
End-to-end API chain test.
Runs: profile ingest → job canonicalize → tailor → decision engine
"""
import asyncio
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

RESUME_TEXT = """
LOKANTH SRIHARI
lokanth2006@gmail.com | 8838379971 | LinkedIn | GitHub

EDUCATION
Integrated M.Tech in Computer Science Engineering (Data Science) - Vellore Institute of Technology (Expected May 2028), CGPA: 8.44/10

PROJECTS & EXPERIENCE
Machine Learning & Backend Developer - Customer Intelligence RAG System (Jan 2026 - Feb 2026)
* Built a Retrieval-Augmented Generation (RAG) system to enable natural-language querying over customer feedback data
* Implemented semantic search using Sentence Transformers (all-MiniLM-L6-v2) and FAISS for top-k vector similarity retrieval
* Integrated Google Gemini 2.5 Flash for context-grounded answer generation with prompt constraints to reduce hallucination
* Developed a Streamlit web interface and an asynchronous Telegram bot for multi-channel access

Full Stack & Blockchain Developer - BloodLink (Aug 2025 - Oct 2025)
* Built a full-stack blood donation platform using React, Tailwind, Supabase, and Edge Functions
* Developed Solidity smart contracts for blockchain-based donor certificate verification
* Integrated Google Gemini AI to create a real-time medical assistance chatbot

Machine Learning Engineer - Donor Health Classification (Oct 2025 - Nov 2025)
* Developed an SVM classifier achieving 91.5% accuracy on biochemical donor data
* Implemented preprocessing steps including imputation, label encoding, feature scaling, and outlier handling
* Performed GridSearchCV hyperparameter tuning and built evaluation modules using confusion matrices and F1-scores

SKILLS
Python, Java, JavaScript, SQL, Scikit-Learn, NumPy, Pandas, Matplotlib, Seaborn, GitHub, Supabase, AWS
"""

PROFILE_META = {
    "name": "Lokanth Srihari",
    "first_name": "Lokanth",
    "last_name": "Srihari",
    "email": "lokanth2006@gmail.com",
    "phone": "8838379971",
    "location": "Vellore, Tamil Nadu",
    "linkedin": "https://linkedin.com/in/lokanth",
    "skills": ["Python", "ML", "RAG", "React", "Solidity", "Supabase"],
}

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:500]}")

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}") as r:
        return json.loads(r.read())

def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print('='*55)

def ok(msg): print(f"  OK  {msg}")
def fail(msg): print(f"  XX  {msg}")

# ── Step 1: Ingest resume
section("Step 1: Profile Ingest  POST /api/profile/ingest")
try:
    resp = post("/api/profile/ingest", {"raw_text": RESUME_TEXT, "source_document": "resume"})
    facts_created = resp.get("created", 0)
    ok(f"{facts_created} fact units created")
    for f in resp.get("facts", [])[:5]:
        print(f"    [{f['fact_id']}] ({f['type']}) {f['text'][:60]}")
    if facts_created > 5:
        print(f"    ... and {facts_created - 5} more")
except Exception as e:
    fail(f"Profile ingest failed: {e}")
    sys.exit(1)

# ── Step 2: List facts
section("Step 2: List Facts  GET /api/profile/facts")
try:
    resp = get("/api/profile/facts")
    ok(f"{resp['count']} total facts in store")
except Exception as e:
    fail(f"List facts failed: {e}")

# ── Step 3: Canonicalize a job
section("Step 3: Canonicalize Job  POST /api/jobs/manual")
JOB = {
    "company": "Qdrant AI",
    "role": "ML Engineer - RAG Systems",
    "jd_text": "We are hiring an ML Engineer focused on RAG pipelines, FAISS, Sentence Transformers, and LLM APIs. Python proficiency required.",
    "application_link": "https://qdrant.tech/careers",
}
try:
    resp = post("/api/jobs/manual", JOB)
    record = resp.get("record", {})
    fingerprint = record.get("fingerprint")
    ats_type = record.get("ats_type", "other")
    ok(f"Canonicalized: {record.get('company')} / {record.get('role')}")
    ok(f"Fingerprint: {fingerprint}")
    ok(f"ATS type: {ats_type}")
    ok(f"Source confidence: {record.get('source_confidence')}")
    ok(f"Is new: {resp.get('is_new')}")
except Exception as e:
    fail(f"Canonicalize failed: {e}")
    sys.exit(1)

# ── Step 4: Tailor resume + run verifier
section(f"Step 4: Tailor + Verify  POST /api/tailor/{fingerprint}")
try:
    resp = post(f"/api/tailor/{fingerprint}", {"profile": PROFILE_META})
    gs = resp.get("grounding_score", 0)
    total = resp.get("total_bullets", 0)
    passed = resp.get("passed_bullets", 0)
    dropped = resp.get("dropped_bullets", 0)
    ok(f"Resume ID: {resp.get('resume_id')}")
    ok(f"Grounding Score: {gs:.1%}  ({passed}/{total} bullets, {dropped} dropped)")
    print(f"\n  Verified bullets:")
    for b in resp.get("verified_bullets", []):
        print(f"    - {b['text'][:80]}")
    if resp.get("pdf_path"):
        ok(f"PDF generated: {resp['pdf_path']}")
    else:
        print("  !! PDF not generated (reportlab may need fonts)")
except Exception as e:
    fail(f"Tailor failed: {e}")
    sys.exit(1)

# ── Step 5: Decision engine
section(f"Step 5: Decision Engine  POST /api/execute/path-a/{fingerprint}")
try:
    resp = post(f"/api/execute/path-a/{fingerprint}", {"profile": PROFILE_META})
    route = resp.get("route")
    scores = resp.get("scores", {})
    reason = resp.get("reason", "")
    ok(f"Route decided: {route}")
    ok(f"Grounding:    {scores.get('grounding', 0):.1%}")
    ok(f"Completeness: {scores.get('completeness', 0):.1%}")
    ok(f"Execution:    {scores.get('execution', 0):.1%}")
    if route == "PATH_B":
        print(f"  -> PATH_B reason: {reason}")
    else:
        ok("PATH_A gate cleared — browser autofill would launch here")
except Exception as e:
    fail(f"Execute path-a failed: {e}")

# ── Step 6: Tracker
section("Step 6: Application Tracker  GET /api/tracker")
try:
    resp = get("/api/tracker")
    ok(f"{resp['count']} application records")
    for app in resp.get("applications", []):
        print(f"    [{app.get('status')}] {app.get('job_fingerprint', '')[:20]}...")
except Exception as e:
    fail(f"Tracker failed: {e}")

# ── Step 7: Decision audit log
section("Step 7: Decision Audit Log  GET /api/decisions")
try:
    resp = get("/api/decisions")
    ok(f"{resp['count']} decision log entries")
    for d in resp.get("decisions", [])[:3]:
        print(f"    {d.get('route')} | G={d.get('grounding_score'):.2f} C={d.get('completeness_score'):.2f} E={d.get('execution_score'):.2f}")
        print(f"      reason: {d.get('reason')}")
except Exception as e:
    fail(f"Decisions log failed: {e}")

section("API Chain Test Complete")
