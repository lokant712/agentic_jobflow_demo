# Agentic-JobFlow — README

## Overview

Agentic-JobFlow is a locally-run job application workflow system that reduces time-to-quality-application while enforcing two hard constraints:

1. **Zero Fabrication** — Every resume claim is mechanically grounded and traceable to an immutable Fact ID from your Master Profile Store.
2. **Human Control** — No application is ever submitted automatically. Path A fills forms in a visible browser and waits for you. Path B sends you the PDF via Telegram.

---

## Quick Start

### 1. Clone and Install

```powershell
cd agentic_jobflow_demo
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configure

```powershell
copy .env.example .env
# Edit .env — set at minimum:
#   LLM_PROVIDER=offline      (for local testing without API keys)
#   DATABASE_URL=sqlite+aiosqlite:///./data/jobflow.db
```

### 3. Run

```powershell
python -m backend.app.main
# or:
uvicorn backend.app.main:app --reload
```

Open: http://127.0.0.1:8000/ (status page) | http://127.0.0.1:8000/docs (API docs)

---

## Configuration

All settings are set via `.env` (copy from `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `offline` | `claude` / `gemini` / `offline` |
| `LLM_MODEL` | `claude-sonnet-4-5` | Model name for Tailor Agent |
| `VERIFIER_LLM_PROVIDER` | `offline` | Independent model for Grounding Verifier |
| `VERIFIER_MAX_RETRIES` | `2` | Max bullet regeneration attempts before drop |
| `THRESHOLD_GROUNDING` | `0.95` | Grounding Score threshold for PATH_A |
| `THRESHOLD_COMPLETENESS` | `0.85` | Completeness Score threshold for PATH_A |
| `THRESHOLD_EXECUTION` | `0.90` | Execution Score threshold for PATH_A |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Required for Path B notifications |
| `TELEGRAM_CHAT_ID` | _(empty)_ | Your Telegram chat/user ID |
| `ADZUNA_APP_ID` | _(empty)_ | Adzuna API credentials (Scout Agent) |
| `DATABASE_URL` | SQLite | Database connection string |

---

## System Architecture

```
Master Profile Store (FactUnits: FACT-001, FACT-002, ...)
          │
          ▼
Scout Agent / Gmail Adapter ──► Canonicalization & Dedup Layer
                                        │
                                        ▼
                                  Tailor Agent
                                  (text, [fact_ids])
                                        │
                                        ▼
                               Grounding Verifier (independent)
                                        │
                                        ▼
                               Decision Engine (3-Signal Gate)
                              ┌─────────┴──────────┐
                         PATH_A                  PATH_B
                   (Playwright fill)        (Telegram PDF)
                   [Human submits]
```

---

## Core Concepts

### Fact Units
Your profile is decomposed into atomic facts, each with an immutable ID:
- `FACT-001` → `"Led migration to microservices, reducing API latency by 40%"` (metric)
- `FACT-002` → `"Python, Spark, Kafka, PostgreSQL"` (tool)

The Tailor Agent can only use facts that exist in your store. It cannot create new ones.

### Grounding Verifier
Every resume bullet is mechanically checked: numbers, tool names, and proper nouns in the bullet text must appear as substrings in the cited fact text. Bullets that fail N=2 retries are **dropped entirely** (never output unverified).

### 3-Signal Decision Gate
Before Path A launches:
- **Grounding Score** (≥0.95): fraction of verified bullets
- **Completeness Score** (≥0.85): profile fields mappable to ATS required fields (pre-computed from manifest)
- **Execution Score** (≥0.90): ATS identified + no bot challenge + all selectors found

Any single failure → Path B.

### Path A Safety
- Non-headless browser (you see everything)
- Red banner injected and re-injected on every page navigation: **"⚠️ AGENTIC-JOBFLOW — DO NOT SUBMIT"**
- Submit button click-blocked at JS event level
- Field registration verified (DOM events dispatched, value read-back confirmed)
- CAPTCHA/Cloudflare detection → immediate abort to Path B
- **PATH A IS TEST-ONLY until ToS review for Greenhouse/Lever/Ashby is complete**

---

## API Reference

All endpoints are documented at `/docs` (Swagger UI).

### Profile
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/profile/ingest` | Ingest raw resume text → FactUnits |
| `GET` | `/api/profile/facts` | List all FactUnits |
| `POST` | `/api/profile/facts` | Manually create a FactUnit |
| `PUT` | `/api/profile/facts/{id}` | Update a FactUnit |
| `DELETE` | `/api/profile/facts/{id}` | Delete a FactUnit |

### Jobs
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs/scout` | Search for jobs via Scout Agent |
| `POST` | `/api/jobs/scrape` | Scrape a single job URL |
| `POST` | `/api/jobs/gmail-sync` | Sync job alerts from Gmail |
| `POST` | `/api/jobs/manual` | Add a job manually |
| `GET` | `/api/jobs` | List all canonical job records |

### Tailor & Execute
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/tailor/{fingerprint}` | Tailor resume + run Grounding Verifier |
| `POST` | `/api/execute/path-a/{fingerprint}` | Launch Playwright auto-fill |
| `POST` | `/api/execute/path-b/{fingerprint}` | Send Telegram notification |
| `GET` | `/api/tracker` | Application status board |
| `PATCH` | `/api/tracker/{fingerprint}` | Update user-reported outcome |
| `GET` | `/api/decisions` | Decision Engine audit log |

---

## Running Tests

```powershell
pytest tests/ -v --tb=short
```

Expected test coverage:
- `test_profile_service.py` — Fact ID ingestion, one-way flow enforcement
- `test_canonicalizer.py` — Fingerprint determinism, dedup, ATS detection
- `test_grounding_verifier.py` — Entity extraction, pass/fail/drop logic
- `test_decision_engine.py` — Score computation, routing, threshold behavior

---

## Gmail OAuth Setup

1. Create a Google Cloud project and enable the Gmail API.
2. Create OAuth 2.0 credentials (Desktop App).
3. Download as `credentials/gmail_oauth_credentials.json`.
4. On first run of `/api/jobs/gmail-sync`, a browser window will open for OAuth consent.
5. Token is saved to `credentials/gmail_token.json` for subsequent runs.

Scope: `gmail.readonly` only. No email is ever sent or modified.

---

## Telegram Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram → create a bot → get token.
2. Message your bot once, then get your chat ID from `https://api.telegram.org/bot{TOKEN}/getUpdates`.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

---

## Open Items (Pre-Production)

- [ ] **ToS review** for Greenhouse, Lever, Ashby automated form-filling (not submission)
- [ ] **50-pair grounding test set** validation (to be supplied separately — see PRD §5)
- [ ] Full dashboard (React/Vite) — deferred until grounding accuracy validated on test set

---

## Data

All data is stored locally:
- **Database**: `data/jobflow.db` (SQLite)
- **Resumes**: `data/resumes/` (PDF files)
- **Audit logs**: `data/logs/` (JSONL files)
- **Gmail token**: `credentials/gmail_token.json`

No user data leaves your machine except:
- LLM API calls (if using Claude/Gemini provider, not offline)
- Telegram notifications (Path B)
