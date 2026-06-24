from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DiffResult(str, Enum):
    NEW = "NEW"
    CHANGED = "CHANGED"
    NOOP = "NOOP"
    REMOVE = "REMOVE"


@dataclass(frozen=True)
class HashComparison:
    member_id: str
    group_id: str
    diff_result: DiffResult
    incoming_hash: str
    baseline_hash: str | None
    changed_fields: list[str]
    comparison_field_count: int
    excluded_volatile_fields: list[str]
    computed_at: datetime = field(default_factory=datetime.utcnow)

    def to_event(self) -> dict[str, Any]:
        return {
            "memberId": self.member_id,
            "groupId": self.group_id,
            "diffResult": self.diff_result.value,
            "incomingHash": self.incoming_hash,
            "baselineHash": self.baseline_hash,
            "changedFields": self.changed_fields,
            "comparisonFieldCount": self.comparison_field_count,
            "excludedVolatileFields": self.excluded_volatile_fields,
            "computedAt": self.computed_at.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_event())
