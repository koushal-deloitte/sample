"""Human escalation tool — creates a ticket and marks the fallout for human review."""
import logging
import uuid
from typing import Any

log = logging.getLogger(__name__)

TOOL_DEFINITION = {
    "name": "escalate",
    "description": (
        "Escalate the fallout to a human agent by creating a support ticket. "
        "Use this when the fallout cannot be resolved automatically."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why this fallout cannot be resolved automatically"},
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
                "description": "Ticket priority",
            },
            "summary": {"type": "string", "description": "Brief summary of the subscriber situation for the human agent"},
        },
        "required": ["reason", "priority", "summary"],
    },
}


async def execute(input: dict[str, Any], transaction_id: str, dry_run: bool = False) -> dict[str, Any]:
    reason = input["reason"]
    priority = input["priority"]
    summary = input["summary"]
    ticket_id = str(uuid.uuid4())

    log.info(
        "escalation_created",
        extra={
            "transaction_id": transaction_id,
            "ticket_id": ticket_id,
            "priority": priority,
            "reason": reason,
        },
    )

    if dry_run:
        return {"dry_run": True, "ticket_id": ticket_id, "priority": priority, "reason": reason}

    # STUB: Replace with actual ticketing system API call (ServiceNow, Jira, etc.)
    return {
        "ticket_id": ticket_id,
        "status": "created",
        "priority": priority,
        "message": f"Escalation ticket created. Human agent will review transaction {transaction_id}.",
    }
