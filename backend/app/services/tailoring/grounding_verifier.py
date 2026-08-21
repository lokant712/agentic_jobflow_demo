"""
Grounding Verifier — FR-6.x

INDEPENDENT module from the Tailor Agent (separate LLM config or pure heuristic).
Mechanically verifies that every key entity in a generated bullet
traces back to at least one cited FactUnit's text.

Algorithm:
  1. Extract key entities from bullet text: numbers, known tools/tech, capitalized nouns.
  2. For each cited fact_id, retrieve FactUnit.text.
  3. Check: every entity must appear (case-insensitive substring) in at least one cited fact.
  4. Pass/fail per bullet; Grounding Score = passed / total.
  5. On fail: retry generation up to N=2 times.
  6. If still failing after N retries → drop bullet, log reason.
  7. Write VerificationLog row per bullet.

FR-6.3: Regenerate failing bullets (up to N attempts), then drop.
FR-6.4: Log every pass/fail with claim and reason.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import TailoredResume
from backend.app.services.profile_service import resolve_facts

log = logging.getLogger("jobflow.verifier")


# ─── Curated tech/tool entity list ────────────────────────────────────────────
# This list is used to recognize tool names that might not be capitalized.
_KNOWN_TOOLS = {
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#", "ruby",
    "swift", "kotlin", "scala", "r", "sql", "nosql", "postgresql", "postgres", "mysql",
    "sqlite", "mongodb", "redis", "elasticsearch", "cassandra",
    "aws", "gcp", "azure", "docker", "kubernetes", "k8s", "terraform", "ansible",
    "react", "vue", "angular", "nextjs", "fastapi", "django", "flask", "rails",
    "spark", "kafka", "airflow", "dbt", "snowflake", "bigquery", "redshift",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "git", "github", "gitlab", "jira", "confluence", "figma", "tableau", "power bi",
    "salesforce", "hubspot", "zendesk",
    "linux", "unix", "bash", "powershell",
    "graphql", "rest", "grpc", "oauth",
}


# ─── Entity Extraction ────────────────────────────────────────────────────────

def extract_key_entities(text: str) -> list[str]:
    """
    Extract key entities from a bullet text:
      - Numeric strings (e.g., "50%", "3x", "$2M", "200ms")
      - Known tool/tech names
      - Capitalized multi-word nouns (proper nouns, product names)
    """
    entities = []

    # Numeric strings with units
    numeric_pattern = re.compile(
        r"\b\d+[\.,]?\d*\s*"
        r"(?:%|percent|x|×|k|m|b|ms|s|sec|min|hr|day|week|month|year|"
        r"users|customers|requests|queries|transactions|points|"
        r"basis\s+points|nps|arpu|mrr|arr|usd|eur|gbp|\$|€|£)?",
        re.IGNORECASE,
    )
    for m in numeric_pattern.finditer(text):
        entity = m.group().strip()
        if entity and re.search(r"\d", entity):
            entities.append(entity.lower())

    # Known tools (case-insensitive substring)
    text_lower = text.lower()
    for tool in _KNOWN_TOOLS:
        pattern = r"\b" + re.escape(tool) + r"\b"
        if re.search(pattern, text_lower):
            entities.append(tool)

    # Capitalized multi-word sequences (proper nouns / product names)
    # Require at least 2 consecutive capitalized words to avoid capturing
    # sentence-starting verbs (e.g. 'Collaborated', 'Increased').
    capitalized = re.findall(r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)+\b", text)
    for cap in capitalized:
        if len(cap) > 5:
            entities.append(cap.lower())

    return list(set(entities))


# ─── Entity Grounding Check ───────────────────────────────────────────────────

def check_entity_grounded(entity: str, fact_texts: list[str]) -> bool:
    """
    Returns True if entity appears (case-insensitive substring) in at least one fact text.
    """
    entity_lower = entity.lower()
    for text in fact_texts:
        if entity_lower in text.lower():
            return True
    return False


# ─── Verification Result Types ────────────────────────────────────────────────

@dataclass
class BulletVerificationResult:
    bullet_text: str
    fact_ids: list[str]
    entities_checked: list[str]
    passed: bool
    failed_entities: list[str]
    reason: str
    attempt_number: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ResumeVerificationResult:
    resume_id: str
    job_fingerprint: str
    total_bullets: int
    passed_bullets: int
    dropped_bullets: int
    grounding_score: float
    bullet_results: list[BulletVerificationResult]
    verified_bullets: list[dict]  # bullets that passed (to replace unverified ones in resume)


# ─── Audit Logger ─────────────────────────────────────────────────────────────

def _write_verification_log(result: BulletVerificationResult, resume_id: str) -> None:
    """FR-6.4: Log every verification pass/fail with specific claim and reason."""
    settings = get_settings()
    log_path = Path(settings.log_dir) / "grounding_verifier_audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": result.timestamp,
        "resume_id": resume_id,
        "bullet_text": result.bullet_text[:200],
        "fact_ids": result.fact_ids,
        "entities_checked": result.entities_checked,
        "passed": result.passed,
        "failed_entities": result.failed_entities,
        "reason": result.reason,
        "attempt_number": result.attempt_number,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Single-bullet verifier ───────────────────────────────────────────────────

async def _verify_bullet(
    db: AsyncSession,
    bullet: dict,
    attempt: int,
) -> BulletVerificationResult:
    """Verify a single bullet. Returns a BulletVerificationResult."""
    text = bullet.get("text", "")
    fact_ids = bullet.get("fact_ids", [])

    # Resolve cited facts
    facts = await resolve_facts(db, fact_ids)
    fact_texts = [f.text for f in facts]

    # Missing fact IDs → immediate fail
    missing_ids = [fid for fid in fact_ids if fid not in {f.fact_id for f in facts}]
    if missing_ids:
        result = BulletVerificationResult(
            bullet_text=text,
            fact_ids=fact_ids,
            entities_checked=[],
            passed=False,
            failed_entities=missing_ids,
            reason=f"fact_ids_not_found_in_profile: {missing_ids}",
            attempt_number=attempt,
        )
        return result

    # Extract and check entities
    entities = extract_key_entities(text)

    if not entities:
        # No extractable entities — pass (bullet is generic enough to be unverifiable; logged)
        return BulletVerificationResult(
            bullet_text=text,
            fact_ids=fact_ids,
            entities_checked=[],
            passed=True,
            failed_entities=[],
            reason="no_key_entities_extracted_pass",
            attempt_number=attempt,
        )

    failed = [e for e in entities if not check_entity_grounded(e, fact_texts)]

    if failed:
        return BulletVerificationResult(
            bullet_text=text,
            fact_ids=fact_ids,
            entities_checked=entities,
            passed=False,
            failed_entities=failed,
            reason=f"entities_not_in_cited_facts: {failed}",
            attempt_number=attempt,
        )

    return BulletVerificationResult(
        bullet_text=text,
        fact_ids=fact_ids,
        entities_checked=entities,
        passed=True,
        failed_entities=[],
        reason="all_entities_grounded",
        attempt_number=attempt,
    )


# ─── Retry Logic ─────────────────────────────────────────────────────────────

async def _regenerate_bullet(
    db: AsyncSession,
    job_fingerprint: str,
    failed_bullet: dict,
) -> dict | None:
    """
    Attempt to regenerate a single failing bullet using the Tailor Agent.
    Returns a new bullet dict or None if regeneration fails.
    Kept in a separate function so the verifier doesn't import tailor_agent at module level
    (maintains module independence).
    """
    from backend.app.services.ingestion.canonicalizer import get_job
    from backend.app.services.tailoring.tailor_agent import _build_prompt, _validate_bullets
    from backend.app.services.profile_service import list_facts
    from backend.app.services.llm_client import get_llm_client

    settings = get_settings()
    client = get_llm_client(
        provider=settings.verifier_llm_provider,
        model=settings.verifier_llm_model,
    )

    job = await get_job(db, job_fingerprint)
    if not job:
        return None

    all_facts = await list_facts(db)
    valid_fact_ids = {f.fact_id for f in all_facts}
    facts_payload = [
        {"fact_id": f.fact_id, "type": f.type, "text": f.text}
        for f in all_facts
    ]

    # Narrow regeneration prompt for the specific failing bullet
    regen_prompt = (
        f"The following resume bullet failed grounding verification:\n"
        f"  FAILED BULLET: {failed_bullet['text']}\n"
        f"  CITED FACTS: {failed_bullet['fact_ids']}\n\n"
        f"Rewrite this bullet using ONLY the cited facts, ensuring every entity "
        f"is directly present in the fact text. Do not add new information.\n\n"
        f"FACTS_JSON: {json.dumps(facts_payload)}\n\n"
        f"Output ONE bullet as JSON: {{\"text\": \"...\", \"fact_ids\": [...]}}"
    )

    try:
        raw = await client.complete_json(regen_prompt)
        # Handle both list and single-object responses
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if isinstance(raw, dict):
            valid = _validate_bullets([raw], valid_fact_ids)
            return valid[0] if valid else None
    except Exception as exc:
        log.warning(f"Bullet regeneration failed: {exc}")

    return None


# ─── Main Verifier ────────────────────────────────────────────────────────────

async def verify_resume(
    db: AsyncSession,
    resume: TailoredResume,
) -> ResumeVerificationResult:
    """
    FR-6.1 – FR-6.4: Mechanically verify all bullets in a TailoredResume.

    - Runs entity grounding check per bullet.
    - Retries failing bullets up to max_retries times (default 2).
    - Drops bullets that fail all retries.
    - Updates resume.grounding_score and resume.bullets in place.
    - Returns full verification result for audit.
    """
    settings = get_settings()
    max_retries = settings.verifier_max_retries

    bullets = resume.get_bullets()
    all_results: list[BulletVerificationResult] = []
    verified_bullets: list[dict] = []

    for bullet in bullets:
        final_result: BulletVerificationResult | None = None
        current_bullet = bullet

        for attempt in range(1, max_retries + 2):  # attempts 1 .. max_retries+1
            result = await _verify_bullet(db, current_bullet, attempt)
            _write_verification_log(result, resume.resume_id)

            if result.passed:
                verified_bullets.append(current_bullet)
                final_result = result
                break
            else:
                log.warning(
                    f"Verifier: FAIL (attempt {attempt}) — "
                    f"'{current_bullet['text'][:60]}' — {result.reason}"
                )
                if attempt <= max_retries:
                    # Retry: regenerate the bullet
                    new_bullet = await _regenerate_bullet(
                        db, resume.job_fingerprint, current_bullet
                    )
                    if new_bullet:
                        current_bullet = new_bullet
                    # else keep same bullet to re-verify (will likely fail again)
                else:
                    # FR-6.3: Drop rather than output unverified
                    log.warning(
                        f"Verifier: DROPPED bullet after {max_retries} retries — "
                        f"'{bullet['text'][:60]}'"
                    )
                    final_result = result
                    break

        if final_result:
            all_results.append(final_result)

    total = len(bullets)
    passed = sum(1 for r in all_results if r.passed)
    dropped = total - passed
    grounding_score = round(passed / total, 4) if total > 0 else 0.0

    log.info(
        f"Verifier: {passed}/{total} bullets passed "
        f"(Grounding Score: {grounding_score:.2%}) for resume {resume.resume_id}"
    )

    # FR-6.2: Update grounding score on the resume object
    resume.grounding_score = grounding_score
    resume.set_bullets(verified_bullets)

    return ResumeVerificationResult(
        resume_id=resume.resume_id,
        job_fingerprint=resume.job_fingerprint,
        total_bullets=total,
        passed_bullets=passed,
        dropped_bullets=dropped,
        grounding_score=grounding_score,
        bullet_results=all_results,
        verified_bullets=verified_bullets,
    )
