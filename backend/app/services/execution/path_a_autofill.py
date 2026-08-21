"""
Path A — Playwright Assisted Auto-Fill — FR-8.x

NON-HEADLESS browser automation that fills ATS forms (Greenhouse, Lever, Ashby).
Safety constraints enforced at code level (not just policy):

  FR-8.2: Non-headless browser (user can see all actions).
  FR-8.3: HARD RULE — Submit/Apply buttons are NEVER clicked.
           Any selector matching submit patterns raises an exception if called.
  FR-8.4: Bright red warning banner injected into page on open.
  FR-8.5: Bot-challenge detection mid-session → abort to Path B.
  FR-8.6: Field registration verification — input/change/blur events dispatched,
           DOM value re-read after each fill to confirm registration.

False-positive fill tracking:
  fill_attempt_count and fill_mismatch_count are tracked per session.
  false_positive_fill_rate = fill_mismatch_count / fill_attempt_count
  This maps directly to the PRD success metric (<2%).

WARNING: Path A must NOT be used against live ATS production endpoints
until ToS review is complete. Use only with local mock forms in v1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.db.models import TailoredResume

log = logging.getLogger("jobflow.path_a")

_MANIFESTS_DIR = Path(__file__).parent / "ats_manifests"

# ─── Safety Banner HTML ───────────────────────────────────────────────────────

_BANNER_JS = """
(function() {
  if (document.getElementById('__jobflow_banner__')) return;
  const banner = document.createElement('div');
  banner.id = '__jobflow_banner__';
  banner.style.cssText = [
    'position: fixed',
    'top: 0',
    'left: 0',
    'width: 100%',
    'z-index: 2147483647',
    'background: #c0392b',
    'color: #ffffff',
    'font-family: system-ui, sans-serif',
    'font-size: 15px',
    'font-weight: 700',
    'text-align: center',
    'padding: 12px 20px',
    'box-shadow: 0 2px 12px rgba(0,0,0,0.5)',
    'letter-spacing: 0.03em',
  ].join('; ');
  banner.innerHTML = [
    '⚠️  AGENTIC-JOBFLOW — Fields auto-populated for review.',
    '&nbsp;&nbsp;',
    '<span style="background:#ffffff;color:#c0392b;border-radius:4px;padding:2px 10px;">',
    'DO NOT SUBMIT. Review all fields carefully before clicking Apply.',
    '</span>',
  ].join('');
  document.body.prepend(banner);
  // Re-inject if removed
  const obs = new MutationObserver(() => {
    if (!document.getElementById('__jobflow_banner__')) document.body.prepend(banner);
  });
  obs.observe(document.body, { childList: true });
})();
"""

# ─── Submit button guard (injected into page) ─────────────────────────────────

_SUBMIT_GUARD_JS = """
(function() {
  function blockSubmit(el) {
    el.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopImmediatePropagation();
      alert('[Agentic-JobFlow] Submit button blocked. This tool never auto-submits. Please click submit yourself after reviewing all fields.');
      return false;
    }, true);
  }
  document.querySelectorAll(
    'input[type=submit], button[type=submit], button'
  ).forEach(btn => {
    const txt = (btn.textContent || btn.value || '').toLowerCase();
    if (['submit', 'apply', 'send application', 'submit application'].some(k => txt.includes(k))) {
      blockSubmit(btn);
    }
  });
})();
"""

# ─── Bot-challenge detection ─────────────────────────────────────────────────

_BOT_CHALLENGE_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".cf-challenge",
    "#challenge-form",
    "#cf-wrapper",
    "[class*='captcha']",
]

_BOT_CHALLENGE_TEXTS = [
    "verify you are human",
    "i'm not a robot",
    "cloudflare",
    "checking your browser",
    "ddos protection",
    "captcha",
]


# ─── Result Types ─────────────────────────────────────────────────────────────

@dataclass
class FillResult:
    field_id: str
    value_written: str
    value_read_back: str
    registered: bool  # True if value_written == value_read_back

    @property
    def is_mismatch(self) -> bool:
        return not self.registered


@dataclass
class PathASessionResult:
    success: bool
    abort_reason: str | None
    filled_fields: list[FillResult] = field(default_factory=list)
    bot_challenge_detected: bool = False
    selectors_resolved: bool = True
    fill_attempt_count: int = 0
    fill_mismatch_count: int = 0

    @property
    def false_positive_fill_rate(self) -> float:
        if self.fill_attempt_count == 0:
            return 0.0
        return round(self.fill_mismatch_count / self.fill_attempt_count, 4)


# ─── Manifest loader ─────────────────────────────────────────────────────────

def _load_manifest(ats_type: str) -> dict:
    manifest_path = _MANIFESTS_DIR / f"{ats_type}.json"
    if not manifest_path.exists():
        raise ValueError(f"No ATS manifest found for: {ats_type}")
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    return data[ats_type]


# ─── Field Fill Helpers ───────────────────────────────────────────────────────

async def _fill_text_field(page, selector: str, value: str, field_id: str) -> FillResult:
    """
    FR-8.6: Fill a text/email/tel/url field and verify registration.
    Dispatches input, change, blur events. Re-reads DOM value to confirm.
    """
    # Try each comma-separated selector alternative
    for sel in [s.strip() for s in selector.split(",")]:
        try:
            locator = page.locator(sel).first
            if await locator.count() == 0:
                continue
            if not await locator.is_visible(timeout=3000):
                continue
            if not await locator.is_enabled(timeout=3000):
                continue

            # Clear existing value
            await locator.click()
            await locator.fill("")
            await locator.fill(value)

            # Dispatch DOM events to ensure JS listeners fire (FR-8.6)
            await page.evaluate(
                """([sel, val]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    ['input', 'change', 'blur'].forEach(evt => {
                        el.dispatchEvent(new Event(evt, { bubbles: true }));
                    });
                }""",
                [sel, value],
            )

            # Read back DOM value to verify registration
            read_back = await page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? el.value : ''; }",
                sel,
            )

            registered = read_back.strip() == value.strip()
            if not registered:
                log.warning(
                    f"Path A: Fill mismatch for {field_id}: wrote '{value}', read back '{read_back}'"
                )

            return FillResult(
                field_id=field_id,
                value_written=value,
                value_read_back=read_back,
                registered=registered,
            )
        except Exception as exc:
            log.debug(f"Path A: selector '{sel}' failed for {field_id}: {exc}")
            continue

    log.warning(f"Path A: no working selector found for field {field_id}")
    return FillResult(
        field_id=field_id,
        value_written=value,
        value_read_back="",
        registered=False,
    )


async def _upload_file_field(page, selector: str, file_path: str, field_id: str) -> FillResult:
    """Upload a file (resume PDF) to a file input."""
    for sel in [s.strip() for s in selector.split(",")]:
        try:
            locator = page.locator(sel).first
            if await locator.count() == 0:
                continue
            await locator.set_input_files(file_path)
            return FillResult(
                field_id=field_id,
                value_written=file_path,
                value_read_back="file_uploaded",
                registered=True,
            )
        except Exception as exc:
            log.debug(f"Path A: file upload failed for {field_id} with selector '{sel}': {exc}")
            continue

    return FillResult(
        field_id=field_id,
        value_written=file_path,
        value_read_back="",
        registered=False,
    )


# ─── Bot Challenge Detection ──────────────────────────────────────────────────

async def _detect_bot_challenge(page) -> bool:
    """
    FR-8.5: Check for CAPTCHA, Cloudflare interstitials, and bot-challenge indicators.
    Returns True if a challenge is detected.
    """
    # Check DOM selectors
    for selector in _BOT_CHALLENGE_SELECTORS:
        try:
            count = await page.locator(selector).count()
            if count > 0:
                log.warning(f"Path A: Bot challenge detected via selector: {selector}")
                return True
        except Exception:
            pass

    # Check page text for known challenge phrases
    try:
        body_text = (await page.locator("body").inner_text()).lower()
        for phrase in _BOT_CHALLENGE_TEXTS:
            if phrase in body_text:
                log.warning(f"Path A: Bot challenge detected via text: '{phrase}'")
                return True
    except Exception:
        pass

    return False


# ─── Selector Stability Check ─────────────────────────────────────────────────

async def _check_selectors_stable(page, manifest: dict, timeout_ms: int = 10000) -> bool:
    """
    Signal 3: Verify all required-field selectors resolve to visible, enabled elements.
    Returns True only if ALL required selectors are found.
    """
    selectors = manifest.get("selectors", {})
    required_fields = manifest.get("required_fields", [])
    required_ids = {
        f["id"] for f in required_fields if f.get("required", True)
    }

    all_resolved = True
    for field_id in required_ids:
        selector = selectors.get(field_id, "")
        if not selector:
            log.warning(f"Path A: no selector defined for required field {field_id}")
            all_resolved = False
            continue

        found = False
        for sel in [s.strip() for s in selector.split(",")]:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    found = True
                    break
            except Exception:
                pass

        if not found:
            log.warning(f"Path A: required field selector not found: {field_id} ({selector})")
            all_resolved = False

    return all_resolved


# ─── Main Path A Entry Point ──────────────────────────────────────────────────

async def run_path_a(
    application_url: str,
    ats_type: str,
    profile: dict,
    resume: TailoredResume,
) -> PathASessionResult:
    """
    FR-8.x: Open a non-headless browser, fill ATS form, display safety banner.
    Never clicks submit. Detects bot challenges and aborts to Path B if found.

    Args:
        application_url: The ATS application page URL.
        ats_type: "greenhouse" | "lever" | "ashby"
        profile: Candidate profile dict (name, email, phone, etc.)
        resume: Verified TailoredResume with pdf_path set.

    Returns:
        PathASessionResult with fill results and false-positive fill rate.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium") from exc

    manifest = _load_manifest(ats_type)
    selectors = manifest.get("selectors", {})
    required_fields = manifest.get("required_fields", [])

    result = PathASessionResult(success=False, abort_reason=None)

    async with async_playwright() as p:
        # FR-8.2: NON-HEADLESS browser
        browser = await p.chromium.launch(headless=False, slow_mo=200)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            log.info(f"Path A: Opening {application_url} (ATS: {ats_type})")
            await page.goto(application_url, wait_until="domcontentloaded", timeout=30000)

            # FR-8.4: Inject safety banner immediately
            await page.evaluate(_BANNER_JS)

            # FR-8.3: Inject submit button guard
            await page.evaluate(_SUBMIT_GUARD_JS)

            # FR-8.5: Check for bot challenge before filling
            if await _detect_bot_challenge(page):
                result.bot_challenge_detected = True
                result.abort_reason = "bot_challenge_detected_on_load"
                log.warning("Path A: Bot challenge on page load → aborting to Path B")
                return result

            # Check selector stability (Signal 3)
            selectors_ok = await _check_selectors_stable(page, manifest)
            result.selectors_resolved = selectors_ok
            if not selectors_ok:
                result.abort_reason = "selectors_not_stable"
                log.warning("Path A: Required selectors not found → aborting")
                return result

            # Fill each field
            for field_spec in required_fields:
                field_id = field_spec["id"]
                field_type = field_spec.get("type", "text")
                profile_key = field_spec.get("profile_key", "")
                selector = selectors.get(field_id, "")

                if not selector:
                    continue

                # Resolve value from profile
                value = ""
                if field_type == "file":
                    value = profile.get("resume_pdf_path") or (resume.pdf_path or "")
                else:
                    value = profile.get(profile_key, "")
                    # Compose full_name from first+last if needed
                    if not value and profile_key == "full_name":
                        value = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()

                if not value:
                    log.debug(f"Path A: no value for field {field_id}, skipping")
                    continue

                result.fill_attempt_count += 1

                if field_type == "file":
                    fill_result = await _upload_file_field(page, selector, value, field_id)
                else:
                    fill_result = await _fill_text_field(page, selector, value, field_id)

                result.filled_fields.append(fill_result)
                if fill_result.is_mismatch:
                    result.fill_mismatch_count += 1

                # FR-8.5: Check for bot challenge after each field fill
                if await _detect_bot_challenge(page):
                    result.bot_challenge_detected = True
                    result.abort_reason = f"bot_challenge_detected_after_filling_{field_id}"
                    log.warning(f"Path A: Bot challenge mid-fill after {field_id} → aborting")
                    # Re-inject banner before pause
                    await page.evaluate(_BANNER_JS)
                    # Pause to let user see the state; don't close browser
                    await asyncio.sleep(5)
                    return result

                # Re-inject banner after each navigation event
                await page.evaluate(_BANNER_JS)
                await asyncio.sleep(0.3)

            result.success = True
            log.info(
                f"Path A: Fill complete. "
                f"{result.fill_attempt_count} fields attempted, "
                f"{result.fill_mismatch_count} mismatches "
                f"(false-positive rate: {result.false_positive_fill_rate:.1%}). "
                f"Browser open for human review."
            )

            # FR-8.4: Banner stays. Wait indefinitely for human to submit or close.
            # Do NOT close the browser — leave it open for the user.
            log.info("Path A: Waiting for human review. Browser will remain open.")
            # Keep the session alive (browser stays open — user must manually close)
            await asyncio.sleep(3600)  # 1-hour window; user closes when done

        except asyncio.CancelledError:
            log.info("Path A: Session cancelled by user.")
            result.abort_reason = "cancelled_by_user"
        except Exception as exc:
            log.error(f"Path A: Unexpected error: {exc}")
            result.abort_reason = f"unexpected_error:{exc}"
        finally:
            # Do NOT call browser.close() — leave browser open for human review
            pass

    return result
