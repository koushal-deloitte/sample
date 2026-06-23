import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.models.skill import Skill
from src.skills import router as skill_router

_FAKE_SKILL = Skill(
    id=uuid.uuid4(),
    name="MISSING_NPI Handler",
    description="Handles MISSING_NPI error",
    error_codes=["MISSING_NPI"],
    content="## Instructions\nLook up the NPI.",
    allowed_tools=["call_api"],
)


@pytest.mark.asyncio
async def test_route_matched():
    mock_session = AsyncMock()
    with patch("src.skills.router.SkillRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.get_by_error_code = AsyncMock(return_value=_FAKE_SKILL)
        skill, matched = await skill_router.route("MISSING_NPI", mock_session)
    assert matched is True
    assert skill.name == "MISSING_NPI Handler"


@pytest.mark.asyncio
async def test_route_fallback_when_no_skill():
    mock_session = AsyncMock()
    with patch("src.skills.router.SkillRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.get_by_error_code = AsyncMock(return_value=None)
        skill, matched = await skill_router.route("UNKNOWN_CODE_XYZ", mock_session)
    assert matched is False
    assert skill.name == "UNKNOWN_FALLOUT"
