"""Tests for Master Profile Store (profile_service.py)"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.app.services.profile_service import (
    _segment_resume, _classify_type, ingest_resume,
    list_facts, create_fact_unit, update_fact_unit, delete_fact_unit, resolve_facts
)


# ─── Segmentation & Classification ───────────────────────────────────────────

def test_segment_resume_basic():
    text = """
    Software Engineer at Acme Corp
    • Led migration of monolith to microservices, reducing latency by 40%
    • Built Python data pipeline processing 2M records/day using Apache Spark
    • Proficient in AWS, Docker, Kubernetes
    """
    segments = _segment_resume(text)
    assert len(segments) >= 3
    assert any("40%" in s for s in segments)
    assert any("2M records" in s or "2M" in s for s in segments)


def test_segment_resume_discards_short_lines():
    text = "OK\n• x\n• Led team of 8 engineers to deliver product on time and under budget"
    segments = _segment_resume(text)
    assert all(len(s) >= 20 for s in segments)


def test_segment_resume_discards_uppercase_headers():
    text = "EXPERIENCE\n• Designed and deployed REST APIs serving 50k daily active users"
    segments = _segment_resume(text)
    assert all("EXPERIENCE" not in s for s in segments)


def test_classify_type_metric():
    assert _classify_type("Increased revenue by 35%") == "metric"
    assert _classify_type("Reduced latency by 200ms") == "metric"
    assert _classify_type("Served 1.2M users") == "metric"


def test_classify_type_tool():
    assert _classify_type("Proficient in Python and PostgreSQL") == "tool"
    assert _classify_type("Used AWS Lambda for serverless architecture") == "tool"


def test_classify_type_outcome():
    assert _classify_type("Delivered the product two weeks early") == "outcome"
    assert _classify_type("Reduced operational costs by improving automation") == "outcome"


def test_classify_type_responsibility_fallback():
    assert _classify_type("Collaborated with cross-functional team on project planning") == "responsibility"


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_creates_facts_with_sequential_ids():
    """FR-1.1, FR-1.2: Ingest creates FactUnits with sequential FACT-NNN IDs."""
    from unittest.mock import AsyncMock, MagicMock, patch

    resume_text = (
        "• Led a team of 6 engineers building high-throughput data pipelines\n"
        "• Reduced infrastructure costs by 30% via spot instance optimization\n"
        "• Expert in Python, Spark, Kafka, and AWS infrastructure\n"
        "• Delivered recommendation engine serving 500k daily active users\n"
    )

    db = AsyncMock()
    # Mock the count query to return 0 (no existing facts)
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 0
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    added = []
    def capture_add(obj):
        added.append(obj)
    db.add = capture_add

    # Patch _next_fact_id to return sequential IDs
    call_count = [0]
    async def mock_next_id(db):
        call_count[0] += 1
        return f"FACT-{call_count[0]:03d}"

    with patch("backend.app.services.profile_service._next_fact_id", side_effect=mock_next_id):
        facts = await ingest_resume(db, resume_text, "test_resume.pdf")

    assert len(facts) >= 3
    # IDs must be in FACT-NNN format
    for fact in facts:
        assert fact.fact_id.startswith("FACT-")
    # Source document set correctly
    for fact in facts:
        assert fact.source_document == "test_resume.pdf"


@pytest.mark.asyncio
async def test_one_way_flow_enforced():
    """FR-1.4: Tailor Agent cannot create FactUnits (must not import or call create_fact_unit)."""
    import ast
    import inspect
    from backend.app.services.tailoring import tailor_agent
    source = inspect.getsource(tailor_agent)
    tree = ast.parse(source)

    # Check for any Call node whose func name or attr is "create_fact_unit"
    create_fact_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "create_fact_unit":
                create_fact_calls.append(node)
            elif isinstance(func, ast.Attribute) and func.attr == "create_fact_unit":
                create_fact_calls.append(node)

    assert len(create_fact_calls) == 0, (
        f"tailor_agent.py must NOT call create_fact_unit() — "
        f"one-way flow violation! Found {len(create_fact_calls)} call(s)."
    )


@pytest.mark.asyncio
async def test_resolve_facts_returns_only_existing():
    """resolve_facts returns only facts that exist; missing IDs silently omitted."""
    db = AsyncMock()
    from backend.app.db.models import FactUnit

    mock_fact = FactUnit(fact_id="FACT-001", type="metric", text="Increased revenue by 50%", source_document="resume")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_fact]
    db.execute = AsyncMock(return_value=mock_result)

    result = await resolve_facts(db, ["FACT-001", "FACT-999"])
    assert len(result) == 1
    assert result[0].fact_id == "FACT-001"
