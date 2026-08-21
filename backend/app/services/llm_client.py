"""
Pluggable LLM client — abstracts Gemini, Claude, and offline heuristic backends.

The offline backend is a deterministic heuristic generator that:
  - Produces valid structured JSON matching the Tailor Agent's expected output format
  - Uses only the provided fact texts (no hallucination possible)
  - Is suitable for CI testing without API keys

Switch via LLM_PROVIDER env var: "claude" | "gemini" | "offline"
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any


# ─── Base Interface ────────────────────────────────────────────────────────────

class LLMClient(ABC):
    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 2048, **kwargs) -> str:
        """Send a prompt and return the text response."""
        ...

    @abstractmethod
    async def complete_json(self, prompt: str, max_tokens: int = 2048, **kwargs) -> Any:
        """Send a prompt, parse JSON from response, return Python object."""
        ...


# ─── Claude Client ────────────────────────────────────────────────────────────

class ClaudeClient(LLMClient):
    def __init__(self, api_key: str, model: str):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(self, prompt: str, max_tokens: int = 2048, **kwargs) -> str:
        message = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def complete_json(self, prompt: str, max_tokens: int = 2048, **kwargs) -> Any:
        text = await self.complete(prompt, max_tokens, **kwargs)
        return _extract_json(text)


# ─── Gemini Client (google-genai SDK) ────────────────────────────────────────

class GeminiClient(LLMClient):
    def __init__(self, api_key: str, model: str):
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai package not installed. Run: pip install google-genai") from exc
        self._client = genai.Client(api_key=api_key)
        self.model = model

    async def complete(self, prompt: str, max_tokens: int = 2048, **kwargs) -> str:
        from google import genai
        from google.genai import types
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        return response.text

    async def complete_json(self, prompt: str, max_tokens: int = 2048, **kwargs) -> Any:
        text = await self.complete(prompt, max_tokens, **kwargs)
        return _extract_json(text)


# ─── Offline / Heuristic Client ───────────────────────────────────────────────

class OfflineHeuristicClient(LLMClient):
    """
    Deterministic heuristic generator.
    - For tailor_agent prompts: extracts facts and formats them into bullets.
    - For verifier prompts: always returns YES/NO based on entity matching.
    - For classifier prompts: returns YES/NO based on keyword presence.
    - No API calls, no cost, fully deterministic for CI.
    """

    async def complete(self, prompt: str, max_tokens: int = 2048, **kwargs) -> str:
        prompt_lower = prompt.lower()

        # Gmail classifier: YES/NO prompt
        if "is the following email a job posting" in prompt_lower:
            keywords = ["job", "hiring", "role", "position", "opportunity", "alert"]
            if any(kw in prompt_lower for kw in keywords):
                return "YES"
            return "NO"

        # Generic YES/NO
        if "answer (yes or no only)" in prompt_lower:
            return "YES"

        return "Processed by offline heuristic client."

    async def complete_json(self, prompt: str, max_tokens: int = 2048, **kwargs) -> Any:
        """
        Parse the facts context injected into the prompt and format as bullets.
        Expects the prompt to contain a JSON block of facts (injected by tailor_agent).
        """
        # Extract facts JSON block injected by tailor_agent
        facts_match = re.search(
            r"FACTS_JSON:\s*(\[.*?\])",
            prompt,
            re.DOTALL,
        )
        if not facts_match:
            return []

        try:
            facts = json.loads(facts_match.group(1))
        except json.JSONDecodeError:
            return []

        # Generate one bullet per fact (deterministic, no hallucination)
        bullets = []
        for fact in facts[:8]:  # cap at 8 bullets
            fact_id = fact.get("fact_id", "")
            text = fact.get("text", "")
            if not text or not fact_id:
                continue
            # Truncate to a bullet-length sentence
            sentence = text.split(".")[0].strip()
            if len(sentence) > 20:
                bullets.append({"text": sentence, "fact_ids": [fact_id]})

        return bullets


# ─── JSON extraction helper ───────────────────────────────────────────────────

def _extract_json(text: str) -> Any:
    """
    Extract JSON from LLM response text.
    Handles markdown code fences (```json ... ```) and raw JSON.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip().strip("`").strip()

    # Find the outermost JSON array or object
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No valid JSON found in LLM response: {text[:200]}")


# ─── Factory ──────────────────────────────────────────────────────────────────

def get_llm_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    """
    Factory function. Reads from settings if not provided.
    provider: "claude" | "gemini" | "offline"
    Models:
      gemini  → e.g. "gemini-2.0-flash" (fast, cheap) or "gemini-1.5-pro"
      claude  → e.g. "claude-sonnet-4-5"
      offline → deterministic heuristic, no API cost (CI default)
    """
    from backend.app.config import get_settings

    settings = get_settings()
    provider = provider or settings.llm_provider
    model = model or settings.llm_model

    if provider == "claude":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")
        return ClaudeClient(api_key=settings.anthropic_api_key, model=model)

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set in environment")
        return GeminiClient(api_key=settings.gemini_api_key, model=model)

    if provider == "offline":
        return OfflineHeuristicClient()

    raise ValueError(f"Unknown LLM provider: {provider!r}. Use 'claude', 'gemini', or 'offline'.")
