"""
Agentic-JobFlow application configuration.
All settings are loaded from environment variables / .env file.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ─────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "offline"  # "claude" | "gemini" | "offline"
    llm_model: str = "claude-sonnet-4-5"

    verifier_llm_provider: str = "offline"
    verifier_llm_model: str = "claude-haiku-4-5"
    verifier_max_retries: int = 2

    # ── Decision Engine Thresholds ───────────────────────────────────────────
    threshold_grounding: float = 0.95
    threshold_completeness: float = 0.85
    threshold_execution: float = 0.90

    # ── Gmail ────────────────────────────────────────────────────────────────
    gmail_credentials_path: str = "credentials/gmail_oauth_credentials.json"
    gmail_token_path: str = "credentials/gmail_token.json"

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Scout Agent ──────────────────────────────────────────────────────────
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/jobflow.db"

    # ── Storage ──────────────────────────────────────────────────────────────
    resume_output_dir: str = "./data/resumes"
    log_dir: str = "./data/logs"

    # ── App ──────────────────────────────────────────────────────────────────
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
