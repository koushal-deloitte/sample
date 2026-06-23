import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


_PHI_FIELDS = {"ssn", "social_security_number", "dob", "date_of_birth", "first_name", "last_name", "name"}


def mask_phi(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively mask PHI fields before logging."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _PHI_FIELDS:
            result[key] = "***"
        elif isinstance(value, dict):
            result[key] = mask_phi(value)
        else:
            result[key] = value
    return result


class FalloutRecord(BaseModel):
    transaction_id: str
    error_code: str
    error_message: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    raw_sqs_body: dict[str, Any] = Field(default_factory=dict)

    def safe_log_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "received_at": self.received_at.isoformat(),
        }


class SubscriberData(BaseModel):
    transaction_id: str
    raw: dict[str, Any]

    def safe_log_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "data": mask_phi(self.raw),
        }


class FalloutRunResult(BaseModel):
    run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    transaction_id: str
    error_code: str
    error_message: str
    skill_id: uuid.UUID | None = None
    status: str  # resolved | escalated | failed
    resolution_notes: str = ""
    tool_call_count: int = 0
    llm_calls: int = 0
    duration_ms: int = 0
