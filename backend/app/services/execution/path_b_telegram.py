"""
Path B — Telegram Fallback Notification — FR-9.x

Sends a Telegram message with:
  - Job summary (company, role, application link)
  - Reason for fallback (which score failed and its value)
  - Tailored resume PDF as attachment

FR-9.1: Telegram Bot API — sendMessage + sendDocument
FR-9.2: Failure reason included in notification
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from backend.app.config import get_settings
from backend.app.db.models import TailoredResume
from backend.app.services.decision_engine import RoutingDecision

log = logging.getLogger("jobflow.path_b")

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _format_score_bar(score: float, threshold: float, label: str) -> str:
    """Visual score bar for Telegram message."""
    pct = int(score * 10)
    bar = "█" * pct + "░" * (10 - pct)
    status = "✅" if score >= threshold else "❌"
    return f"{status} {label}: [{bar}] {score:.1%} (threshold: {threshold:.0%})"


def _build_message(
    company: str,
    role: str,
    application_link: str,
    decision: RoutingDecision,
) -> str:
    """Build the Telegram notification message with score details."""
    settings = get_settings()

    scores_text = "\n".join([
        _format_score_bar(decision.grounding_score, decision.threshold_grounding, "Grounding   "),
        _format_score_bar(decision.completeness_score, decision.threshold_completeness, "Completeness"),
        _format_score_bar(decision.execution_score, decision.threshold_execution, "Execution   "),
    ])

    lines = [
        "🤖 *Agentic\\-JobFlow — Manual Review Required*",
        "",
        f"🏢 *Company:* {_escape_md(company)}",
        f"💼 *Role:* {_escape_md(role)}",
        "",
        "📊 *Routing Scores \\(PATH\\_B fallback\\):*",
        f"```\n{scores_text}\n```",
        "",
        f"❓ *Fallback reason:* `{_escape_md(decision.reason)}`",
        "",
        f"🔗 *Apply here:* {application_link}",
        "",
        "_Resume PDF attached\\. Review and apply manually\\._",
    ]
    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


async def send_path_b_notification(
    company: str,
    role: str,
    application_link: str,
    decision: RoutingDecision,
    resume: TailoredResume,
) -> bool:
    """
    FR-9.1, FR-9.2: Send Telegram notification with PDF resume.

    Returns True if both message and document were sent successfully.
    """
    settings = get_settings()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning(
            "Path B: Telegram not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing). "
            "Skipping notification."
        )
        return False

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    message_text = _build_message(company, role, application_link, decision)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Send text message
        try:
            msg_resp = await client.post(
                _api_url(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": message_text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": False,
                },
            )
            msg_resp.raise_for_status()
            log.info(f"Path B: Telegram message sent for {company}/{role}")
        except httpx.HTTPStatusError as exc:
            log.error(f"Path B: Telegram sendMessage failed: {exc.response.text}")
            return False
        except Exception as exc:
            log.error(f"Path B: Telegram sendMessage error: {exc}")
            return False

        # 2. Send PDF resume as document attachment
        if resume.pdf_path and Path(resume.pdf_path).exists():
            try:
                pdf_path = Path(resume.pdf_path)
                caption = (
                    f"📄 Tailored resume for {company} — {role}\n"
                    f"Grounding Score: {decision.grounding_score:.1%}"
                )
                with open(pdf_path, "rb") as pdf_file:
                    doc_resp = await client.post(
                        _api_url(token, "sendDocument"),
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": (pdf_path.name, pdf_file, "application/pdf")},
                    )
                doc_resp.raise_for_status()
                log.info(f"Path B: Resume PDF sent ({pdf_path.name})")
            except httpx.HTTPStatusError as exc:
                log.error(f"Path B: Telegram sendDocument failed: {exc.response.text}")
                return False
            except Exception as exc:
                log.error(f"Path B: Telegram sendDocument error: {exc}")
                return False
        else:
            log.warning(f"Path B: No PDF available for {company}/{role}, sending message only")

    return True
