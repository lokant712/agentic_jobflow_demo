"""
Gmail Adapter — FR-3.x

Read-only Gmail integration (gmail.readonly scope only).
Uses a two-tier classifier to identify job-alert emails:

  Tier 1 (rule-based, zero LLM cost):
    - Sender domain allowlist (LinkedIn, Indeed, Greenhouse, etc.)
    - Subject keyword match
    - Handles ~80% of volume

  Tier 2 (LLM classify, triggered on ambiguous cases only):
    - YES/NO structured prompt against cheap model
    - Conservative: only YES triggers extraction
    - All assessments written to audit log

FR-3.4: Never sends or modifies email. Read-only.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import get_settings

log = logging.getLogger("jobflow.gmail")


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class EmailRecord:
    message_id: str
    sender: str
    subject: str
    body_text: str
    received_at: datetime


@dataclass
class GmailClassificationResult:
    message_id: str
    sender: str
    subject: str
    tier_used: int           # 1 or 2
    classified_as_job: bool
    confidence: str          # "high" | "medium" | "low"
    reason: str


# ─── Tier 1: Rule-Based Classifier ────────────────────────────────────────────

_JOB_SENDER_ALLOWLIST = {
    "jobalert@linkedin.com",
    "jobs-noreply@linkedin.com",
    "noreply@greenhouse.io",
    "alerts@indeed.com",
    "noreply@lever.co",
    "jobs@ashbyhq.com",
    "noreply@glassdoor.com",
    "noreply@builtin.com",
    "jobs@wellfound.com",
    "alerts@ziprecruiter.com",
    "noreply@dice.com",
    "jobs@ycombinator.com",
    "noreply@workday.com",
}

_JOB_SENDER_DOMAIN_ALLOWLIST = {
    "linkedin.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "indeed.com",
    "glassdoor.com",
}

_JOB_SUBJECT_KEYWORDS = re.compile(
    r"\b(job alert|new role|new opening|job opening|is hiring|we.re hiring|"
    r"apply now|job match|opportunity|position|vacancy|career|"
    r"recruitment|talent|full.time|part.time|internship|contract role)\b",
    re.IGNORECASE,
)


def _tier1_classify(sender: str, subject: str) -> tuple[bool, str] | None:
    """
    Returns (is_job, reason) if Tier 1 can make a definitive call, else None.
    None means Tier 2 should be invoked.
    """
    sender_lower = sender.lower()

    # Definitive YES: known sender + keyword in subject
    if sender_lower in _JOB_SENDER_ALLOWLIST:
        return True, "sender_allowlist_exact_match"

    # Check sender domain
    sender_domain = sender_lower.split("@")[-1] if "@" in sender_lower else ""
    if any(allowed in sender_domain for allowed in _JOB_SENDER_DOMAIN_ALLOWLIST):
        return True, f"sender_domain_match:{sender_domain}"

    # Subject keyword match without known sender → inconclusive (Tier 2)
    if _JOB_SUBJECT_KEYWORDS.search(subject):
        return None, "subject_keyword_match_without_known_sender"

    # No match at all → definitive NO (not a job email)
    return False, "no_sender_or_subject_match"


# ─── Tier 2: LLM Classifier ───────────────────────────────────────────────────

async def _tier2_classify(sender: str, subject: str, body_preview: str) -> tuple[bool, str]:
    """
    Single YES/NO structured LLM prompt.
    Conservative: only explicit 'YES' classifies as job alert.
    Returns (is_job, reason).
    """
    from backend.app.services.llm_client import get_llm_client

    settings = get_settings()
    client = get_llm_client(
        provider=settings.verifier_llm_provider,
        model=settings.verifier_llm_model,
    )

    prompt = (
        "You are a classifier. Respond with exactly one word: YES or NO.\n\n"
        "Is the following email a job posting notification, job alert, or recruitment outreach?\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body preview: {body_preview[:200]}\n\n"
        "Answer (YES or NO only):"
    )

    try:
        response = await client.complete(prompt, max_tokens=5)
        answer = response.strip().upper()
        if answer.startswith("YES"):
            return True, "llm_tier2_yes"
        else:
            return False, "llm_tier2_no_or_ambiguous"
    except Exception as exc:
        log.warning(f"Tier 2 classifier failed ({exc}); defaulting to NO")
        return False, f"llm_tier2_error:{exc}"


# ─── Audit Logger ─────────────────────────────────────────────────────────────

def _write_audit_log(result: GmailClassificationResult) -> None:
    """Write all classification decisions to the audit log. FR-3.x transparency."""
    settings = get_settings()
    log_path = Path(settings.log_dir) / "gmail_classifier_audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message_id": result.message_id,
        "sender": result.sender,
        "subject": result.subject,
        "tier_used": result.tier_used,
        "classified_as_job": result.classified_as_job,
        "confidence": result.confidence,
        "reason": result.reason,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Gmail API Integration ────────────────────────────────────────────────────

def _get_gmail_service():
    """Build authenticated Gmail API service (read-only scope)."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Gmail API libraries not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client"
        ) from exc

    settings = get_settings()
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]  # FR-3.1

    creds = None
    token_path = Path(settings.gmail_token_path)
    credentials_path = Path(settings.gmail_credentials_path)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth credentials not found at {credentials_path}. "
                    "See README for Gmail OAuth setup instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _decode_body(part: dict) -> str:
    """Decode base64url email body part to plain text."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_email_text(payload: dict) -> str:
    """Recursively extract plain text from email MIME payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        return _decode_body(payload)
    if mime_type.startswith("multipart/"):
        parts = payload.get("parts", [])
        for part in parts:
            text = _extract_email_text(part)
            if text:
                return text
    return ""


def _parse_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# ─── Public API ───────────────────────────────────────────────────────────────

async def fetch_and_classify_emails(
    max_results: int = 50,
) -> tuple[list[EmailRecord], list[GmailClassificationResult]]:
    """
    FR-3.1 – FR-3.3: Fetch recent emails, classify via two-tier system,
    return only those classified as job alerts.

    Returns:
        (job_emails, all_classifications)
        all_classifications: complete audit trail (classified + skipped)
    """
    try:
        service = _get_gmail_service()
    except Exception as exc:
        log.error(f"Gmail service initialization failed: {exc}")
        return [], []

    # Fetch recent message IDs
    messages_result = (
        service.users()
        .messages()
        .list(userId="me", maxResults=max_results, labelIds=["INBOX"])
        .execute()
    )
    message_stubs = messages_result.get("messages", [])

    job_emails: list[EmailRecord] = []
    all_classifications: list[GmailClassificationResult] = []

    for stub in message_stubs:
        msg_id = stub["id"]
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

        headers = msg.get("payload", {}).get("headers", [])
        sender = _parse_header(headers, "From")
        subject = _parse_header(headers, "Subject")
        date_str = _parse_header(headers, "Date")
        body_text = _extract_email_text(msg.get("payload", {}))

        # Tier 1 classify
        tier1_result = _tier1_classify(sender, subject)

        if tier1_result is not None:
            # Definitive Tier 1 decision
            is_job, reason = tier1_result
            tier_used = 1
            confidence = "high"
        else:
            # Inconclusive → Tier 2
            is_job, reason = await _tier2_classify(sender, subject, body_text)
            tier_used = 2
            confidence = "medium" if is_job else "low"

        classification = GmailClassificationResult(
            message_id=msg_id,
            sender=sender,
            subject=subject,
            tier_used=tier_used,
            classified_as_job=is_job,
            confidence=confidence,
            reason=reason,
        )
        _write_audit_log(classification)  # FR-3.x: all assessments logged
        all_classifications.append(classification)

        if is_job and body_text:
            try:
                received_at = datetime.now(timezone.utc)  # simplified; full parse is complex
            except Exception:
                received_at = datetime.now(timezone.utc)

            job_emails.append(
                EmailRecord(
                    message_id=msg_id,
                    sender=sender,
                    subject=subject,
                    body_text=body_text,
                    received_at=received_at,
                )
            )

    log.info(
        f"Gmail sync: {len(message_stubs)} emails assessed, "
        f"{len(job_emails)} classified as job alerts "
        f"({sum(1 for c in all_classifications if c.tier_used == 2)} via Tier 2 LLM)"
    )
    return job_emails, all_classifications


def extract_job_from_email(email: EmailRecord) -> dict:
    """
    FR-3.3: Extract job record fields from classified email body.
    Returns a dict compatible with canonicalize_job() kwargs.
    This is a best-effort heuristic extractor; low-confidence extractions
    will have low source_confidence scores after canonicalization.
    """
    text = email.body_text

    # Heuristic patterns for common job alert email formats
    company_patterns = [
        r"(?:at|@|with|from)\s+([A-Z][A-Za-z0-9\s&,.']+?)(?:\s+is\s+hiring|\s+is\s+looking|\s+has\s+a)",
        r"Company:\s*([^\n]+)",
        r"Employer:\s*([^\n]+)",
    ]
    role_patterns = [
        r"(?:Role|Position|Job Title|Title|Opportunity):\s*([^\n]+)",
        r"(?:is hiring|looking for)\s+(?:a\s+)?([A-Z][A-Za-z0-9\s/,-]+?)(?:\s+at|\s+to|\.|$)",
    ]
    link_patterns = [
        r"(https?://(?:jobs\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com)/[^\s\"'>]+)",
        r"Apply(?:\s+here)?:\s*(https?://[^\s\"'>]+)",
        r"(https?://\S+/jobs/\S+)",
    ]

    def first_match(patterns: list[str], text: str) -> str:
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    company = first_match(company_patterns, text)
    role = first_match(role_patterns, text)
    application_link = first_match(link_patterns, text)

    return {
        "company": company or email.sender.split("@")[-1].split(".")[0].title(),
        "role": role or email.subject,
        "jd_text": text[:3000],
        "source_channel": "gmail",
        "application_link": application_link,
    }
