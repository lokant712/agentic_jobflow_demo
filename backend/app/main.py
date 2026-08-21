"""
Agentic-JobFlow — FastAPI Application Entry Point

Assembles all API routes, mounts static files (bare-bones status page),
initializes the database on startup, and configures structured logging.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.config import get_settings
from backend.app.db.database import create_all_tables
from backend.app.api.routes import profile, jobs, tailor_execute

# ─── Logging Configuration ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("jobflow")


# ─── App Factory ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Agentic-JobFlow",
        description=(
            "Fact-ID grounded job application workflow system. "
            "Enforces zero resume fabrication and zero automated submission."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Startup: initialize DB + ensure data directories exist ────────────
    @app.on_event("startup")
    async def on_startup():
        log.info("Agentic-JobFlow starting up...")
        await create_all_tables()
        # Ensure data directories exist
        for path in [settings.resume_output_dir, settings.log_dir, "data"]:
            Path(path).mkdir(parents=True, exist_ok=True)
        log.info(f"Database ready at {settings.database_url}")
        log.info(f"LLM provider: {settings.llm_provider} / model: {settings.llm_model}")
        log.info(f"Verifier LLM: {settings.verifier_llm_provider} / {settings.verifier_llm_model}")
        log.info(
            f"Thresholds — Grounding: {settings.threshold_grounding} | "
            f"Completeness: {settings.threshold_completeness} | "
            f"Execution: {settings.threshold_execution}"
        )
        log.info("Server ready at http://127.0.0.1:8000")
        log.info("Status page: http://127.0.0.1:8000/")
        log.info("API docs:    http://127.0.0.1:8000/docs")

    # ── API Routes ──────────────────────────────────────────────────────────
    app.include_router(profile.router)
    app.include_router(jobs.router)
    app.include_router(tailor_execute.router)

    # ── Static files & PDF Storage ──────────────────────────────────────────
    resume_dir = Path(settings.resume_output_dir)
    resume_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/resumes", StaticFiles(directory=str(resume_dir)), name="resumes")

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
