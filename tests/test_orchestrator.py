import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.fallout import FalloutRecord
from src.models.skill import Skill

_SKILL = Skill(
    id=uuid.uuid4(),
    name="MISSING_NPI Handler",
    description="",
    error_codes=["MISSING_NPI"],
    content="## Instructions",
    allowed_tools=["call_api", "escalate"],
)

_RECORD = FalloutRecord(
    transaction_id="txn-001",
    error_code="MISSING_NPI",
    error_message="NPI not found",
)


@pytest.mark.asyncio
async def test_orchestrator_resolved():
    from src.ehap.client import EHAPResponse
    from src.agent import orchestrator

    mock_session = AsyncMock()
    mock_ehap = AsyncMock()
    mock_ehap.chat = AsyncMock(return_value=EHAPResponse(content="Fallout resolved. NPI updated.", tool_calls=[]))

    with (
        patch("src.agent.orchestrator.SkillRepository") as MockRepo,
        patch("src.agent.orchestrator.skill_router.route", AsyncMock(return_value=(_SKILL, True))),
        patch("src.agent.orchestrator._fetch_subscriber", AsyncMock(return_value=MagicMock(raw={"id": "sub-1"}))),
    ):
        repo_instance = MockRepo.return_value
        repo_instance.is_duplicate_run = AsyncMock(return_value=False)
        repo_instance.log_run = AsyncMock()

        result = await orchestrator.process(_RECORD, mock_ehap, mock_session)

    assert result.status == "resolved"
    assert result.transaction_id == "txn-001"
    assert result.llm_calls == 1


@pytest.mark.asyncio
async def test_orchestrator_skips_duplicate():
    from src.agent import orchestrator

    mock_session = AsyncMock()
    mock_ehap = AsyncMock()

    with (
        patch("src.agent.orchestrator.SkillRepository") as MockRepo,
    ):
        repo_instance = MockRepo.return_value
        repo_instance.is_duplicate_run = AsyncMock(return_value=True)

        result = await orchestrator.process(_RECORD, mock_ehap, mock_session)

    assert result.status == "failed"
    assert "Duplicate" in result.resolution_notes
    mock_ehap.chat.assert_not_called()
