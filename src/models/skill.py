import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class SkillBase(BaseModel):
    name: str
    description: str
    error_codes: list[str]
    content: str
    allowed_tools: list[str] = Field(default_factory=list)


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    error_codes: list[str] | None = None
    content: str | None = None
    allowed_tools: list[str] | None = None
    is_active: bool | None = None
    updated_by: str = "chatbot"


class Skill(SkillBase):
    id: uuid.UUID | None = None
    is_active: bool = True
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SkillVersion(BaseModel):
    id: uuid.UUID
    skill_id: uuid.UUID
    content: str
    version: int
    updated_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DraftSkill(BaseModel):
    id: uuid.UUID
    error_code: str
    suggested_content: str
    trigger_count: int
    status: str  # pending | promoted | rejected
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessage(BaseModel):
    role: str  # user | assistant | tool
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    message: str
    pending_confirmation: bool = False
