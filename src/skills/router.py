import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.skill import Skill
from src.skills.repository import SkillRepository
from src.skills.validator import build_skill_template
from src.models.skill import SkillCreate

log = logging.getLogger(__name__)

_UNKNOWN_FALLOUT_SKILL = Skill(
    name="UNKNOWN_FALLOUT",
    description="Generic fallback for unrecognised error codes",
    error_codes=["*"],
    content="""---
name: UNKNOWN_FALLOUT
description: Handles unrecognised fallout types
error_codes:
  - "*"
allowed_tools:
  - escalate
---

## Objective
This fallout type has no matching skill. Escalate to the human review team immediately with a clear summary of the subscriber record and the error encountered.
""",
    allowed_tools=["escalate"],
)


async def route(error_code: str, session: AsyncSession) -> tuple[Skill, bool]:
    """
    Return (skill, matched) where matched=False means we fell back to the UNKNOWN_FALLOUT sentinel.
    """
    repo = SkillRepository(session)
    skill = await repo.get_by_error_code(error_code)
    if skill:
        log.info("skill_matched", extra={"error_code": error_code, "skill_id": str(skill.id)})
        return skill, True

    log.warning("no_skill_for_error_code", extra={"error_code": error_code})
    return _UNKNOWN_FALLOUT_SKILL, False
