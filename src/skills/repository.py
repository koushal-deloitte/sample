import uuid
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import DraftSkillORM, FalloutFeedbackORM, FalloutRunORM, SkillORM, SkillVersionORM
from src.models.fallout import FalloutRunResult
from src.models.skill import Skill, SkillCreate, SkillUpdate


class SkillRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, skill_id: uuid.UUID) -> Skill | None:
        result = await self.session.execute(select(SkillORM).where(SkillORM.id == skill_id))
        row = result.scalar_one_or_none()
        return Skill.model_validate(row) if row else None

    async def get_by_error_code(self, error_code: str) -> Skill | None:
        result = await self.session.execute(
            select(SkillORM).where(
                SkillORM.is_active.is_(True),
                text(":code = ANY(error_codes)").bindparams(code=error_code),
            )
        )
        row = result.scalar_one_or_none()
        return Skill.model_validate(row) if row else None

    async def list_active(self) -> list[Skill]:
        result = await self.session.execute(
            select(SkillORM).where(SkillORM.is_active.is_(True)).order_by(SkillORM.name)
        )
        return [Skill.model_validate(r) for r in result.scalars()]

    async def create(self, data: SkillCreate, created_by: str = "system") -> Skill:
        skill = SkillORM(
            name=data.name,
            description=data.description,
            error_codes=data.error_codes,
            content=data.content,
            allowed_tools=data.allowed_tools,
            version=1,
        )
        self.session.add(skill)
        await self.session.flush()
        version_snap = SkillVersionORM(
            skill_id=skill.id,
            content=skill.content,
            version=1,
            updated_by=created_by,
        )
        self.session.add(version_snap)
        await self.session.commit()
        await self.session.refresh(skill)
        return Skill.model_validate(skill)

    async def update(self, skill_id: uuid.UUID, data: SkillUpdate) -> Skill | None:
        result = await self.session.execute(select(SkillORM).where(SkillORM.id == skill_id))
        skill = result.scalar_one_or_none()
        if not skill:
            return None
        for field, value in data.model_dump(exclude_none=True, exclude={"updated_by"}).items():
            setattr(skill, field, value)
        skill.version += 1
        skill.updated_at = datetime.utcnow()
        if data.content is not None:
            version_snap = SkillVersionORM(
                skill_id=skill.id,
                content=skill.content,
                version=skill.version,
                updated_by=data.updated_by,
            )
            self.session.add(version_snap)
        await self.session.commit()
        await self.session.refresh(skill)
        return Skill.model_validate(skill)

    async def deactivate(self, skill_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(SkillORM).where(SkillORM.id == skill_id).values(is_active=False).returning(SkillORM.id)
        )
        await self.session.commit()
        return result.scalar_one_or_none() is not None

    async def log_run(self, result: FalloutRunResult) -> None:
        row = FalloutRunORM(
            id=result.run_id,
            transaction_id=result.transaction_id,
            error_code=result.error_code,
            error_message=result.error_message,
            skill_id=result.skill_id,
            status=result.status,
            resolution_notes=result.resolution_notes,
            tool_call_count=result.tool_call_count,
            llm_calls=result.llm_calls,
            duration_ms=result.duration_ms,
        )
        self.session.add(row)
        await self.session.commit()

    async def is_duplicate_run(self, transaction_id: str) -> bool:
        result = await self.session.execute(
            select(FalloutRunORM.id).where(
                FalloutRunORM.transaction_id == transaction_id,
                FalloutRunORM.status.in_(["resolved", "escalated"]),
            )
        )
        return result.scalar_one_or_none() is not None

    async def save_feedback(
        self,
        transaction_id: str,
        run_id: uuid.UUID | None,
        resolution_steps: str,
        outcome: str,
    ) -> None:
        fb = FalloutFeedbackORM(
            transaction_id=transaction_id,
            run_id=run_id,
            resolution_steps=resolution_steps,
            outcome=outcome,
        )
        self.session.add(fb)
        await self.session.commit()

    async def get_unmatched_count_last_24h(self, error_code: str) -> int:
        result = await self.session.execute(
            select(FalloutRunORM).where(
                FalloutRunORM.error_code == error_code,
                FalloutRunORM.skill_id.is_(None),
                FalloutRunORM.created_at >= text("NOW() - INTERVAL '24 hours'"),
            )
        )
        return len(result.scalars().all())

    async def upsert_draft_skill(self, error_code: str) -> None:
        result = await self.session.execute(
            select(DraftSkillORM).where(
                DraftSkillORM.error_code == error_code,
                DraftSkillORM.status == "pending",
            )
        )
        draft = result.scalar_one_or_none()
        if draft:
            draft.trigger_count += 1
        else:
            draft = DraftSkillORM(error_code=error_code, trigger_count=1)
            self.session.add(draft)
        await self.session.commit()
