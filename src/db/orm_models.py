import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class SkillORM(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    error_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    versions: Mapped[list["SkillVersionORM"]] = relationship(back_populates="skill", cascade="all, delete-orphan")


class SkillVersionORM(Base):
    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, default="system")
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    skill: Mapped["SkillORM"] = relationship(back_populates="versions")


class FalloutRunORM(Base):
    __tablename__ = "fallout_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    error_code: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="")
    skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # resolved | escalated | failed
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    human_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())


class FalloutFeedbackORM(Base):
    __tablename__ = "fallout_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("fallout_runs.id"), nullable=True)
    resolution_steps: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())


class DraftSkillORM(Base):
    __tablename__ = "draft_skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    error_code: Mapped[str] = mapped_column(String, nullable=False, index=True)
    suggested_content: Mapped[str] = mapped_column(Text, default="")
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | promoted | rejected
    created_at: Mapped[datetime] = mapped_column(default=func.now())
