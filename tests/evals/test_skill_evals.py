"""
Skill resolution evals — golden test set.

Each fixture defines a synthetic fallout record, the skill that should handle it,
and the expected outcome (resolved or escalated).

These evals require a live EHAP connection; skip with SKIP_EVALS=1 in CI.
"""
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.skill import Skill

pytestmark = pytest.mark.skipif(
    os.getenv("SKIP_EVALS", "1") == "1",
    reason="Skipping live evals (set SKIP_EVALS=0 to enable)",
)

_NPI_SKILL = Skill(
    id=uuid.uuid4(),
    name="MISSING_NPI Handler",
    description="",
    error_codes=["MISSING_NPI"],
    content="""---
name: MISSING_NPI Handler
error_codes:
  - MISSING_NPI
allowed_tools:
  - call_api
  - escalate
---

## Objective
Resolve MISSING_NPI fallouts by looking up the NPI registry API.

## Steps
1. Call GET https://npi-registry.example.com/lookup?subscriber_id={subscriber_id}
2. If NPI found, call POST https://enrollment-api.example.com/update with the NPI.
3. If NPI not found, escalate with priority=medium.
""",
    allowed_tools=["call_api", "escalate"],
    is_active=True,
    version=1,
    created_at=None,  # type: ignore[arg-type]
    updated_at=None,  # type: ignore[arg-type]
)

_GOLDEN_CASES = [
    {
        "description": "NPI lookup succeeds — should resolve",
        "error_code": "MISSING_NPI",
        "subscriber_raw": {"subscriber_id": "S001", "name": "Test User", "npi": None},
        "expected_status": "resolved",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _GOLDEN_CASES, ids=[c["description"] for c in _GOLDEN_CASES])
async def test_skill_eval(case):
    from src.agent import orchestrator
    from src.models.fallout import FalloutRecord
    from src.ehap.client import EHAPClient

    record = FalloutRecord(
        transaction_id=f"eval-{uuid.uuid4()}",
        error_code=case["error_code"],
        error_message="",
    )
    ehap = EHAPClient()
    mock_session = AsyncMock()

    with (
        patch("src.agent.orchestrator.SkillRepository") as MockRepo,
        patch("src.agent.orchestrator.skill_router.route", AsyncMock(return_value=(_NPI_SKILL, True))),
        patch("src.agent.orchestrator._fetch_subscriber", AsyncMock(return_value=MagicMock(raw=case["subscriber_raw"]))),
    ):
        repo_instance = MockRepo.return_value
        repo_instance.is_duplicate_run = AsyncMock(return_value=False)
        repo_instance.log_run = AsyncMock()
        result = await orchestrator.process(record, ehap, mock_session)

    assert result.status == case["expected_status"], (
        f"Expected {case['expected_status']} but got {result.status}. Notes: {result.resolution_notes}"
    )
    await ehap.close()
