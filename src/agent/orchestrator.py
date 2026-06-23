import json
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import guardrails
from src.agent.tools import api_caller, db_tool, escalation
from src.config import settings
from src.ehap.client import EHAPClient, ToolCall
from src.models.fallout import FalloutRecord, FalloutRunResult, SubscriberData
from src.models.skill import Skill
from src.skills import router as skill_router
from src.skills.repository import SkillRepository

log = logging.getLogger(__name__)

_ALL_TOOLS = [api_caller.TOOL_DEFINITION, db_tool.TOOL_DEFINITION, escalation.TOOL_DEFINITION]


async def _fetch_subscriber(transaction_id: str) -> SubscriberData:
    async with httpx.AsyncClient(
        base_url=settings.subscriber_api_base_url,
        headers={"Authorization": f"Bearer {settings.subscriber_api_key}"},
        timeout=30,
    ) as client:
        response = await client.get(f"/subscribers/{transaction_id}")
        response.raise_for_status()
        return SubscriberData(transaction_id=transaction_id, raw=response.json())


async def _execute_tool(
    tool_call: ToolCall,
    transaction_id: str,
    allowed_tools: list[str],
    session: AsyncSession,
) -> dict[str, Any]:
    if not guardrails.is_tool_allowed(tool_call.name, allowed_tools):
        return {"error": f"Tool '{tool_call.name}' is not permitted by this skill."}

    dry_run = settings.dry_run

    if tool_call.name == "call_api":
        return await api_caller.execute(tool_call.input, dry_run=dry_run)
    if tool_call.name == "query_db":
        return await db_tool.execute(tool_call.input, session=session, dry_run=dry_run)
    if tool_call.name == "escalate":
        return await escalation.execute(tool_call.input, transaction_id=transaction_id, dry_run=dry_run)

    return {"error": f"Unknown tool: {tool_call.name}"}


async def process(record: FalloutRecord, ehap: EHAPClient, session: AsyncSession) -> FalloutRunResult:
    run_id = uuid.uuid4()
    start_ms = int(time.monotonic() * 1000)
    repo = SkillRepository(session)
    tool_call_count = 0
    llm_calls = 0

    log.info("fallout_run_start", extra={"run_id": str(run_id), **record.safe_log_dict()})

    # Idempotency check
    if await repo.is_duplicate_run(record.transaction_id):
        log.info("duplicate_run_skipped", extra={"transaction_id": record.transaction_id})
        return FalloutRunResult(
            run_id=run_id,
            transaction_id=record.transaction_id,
            error_code=record.error_code,
            error_message=record.error_message,
            status="failed",
            resolution_notes="Duplicate: already processed.",
        )

    # Enrich with subscriber data
    try:
        subscriber = await _fetch_subscriber(record.transaction_id)
    except Exception as exc:
        log.error("subscriber_fetch_failed", extra={"transaction_id": record.transaction_id, "error": str(exc)})
        return FalloutRunResult(
            run_id=run_id,
            transaction_id=record.transaction_id,
            error_code=record.error_code,
            error_message=record.error_message,
            status="failed",
            resolution_notes=f"Could not fetch subscriber data: {exc}",
        )

    # Route to skill
    skill, matched = await skill_router.route(record.error_code, session)
    if not matched:
        count = await repo.get_unmatched_count_last_24h(record.error_code)
        if count >= settings.unmatched_fallout_threshold:
            await repo.upsert_draft_skill(record.error_code)

    # Build tools filtered by skill allowlist
    allowed = skill.allowed_tools
    tools = [t for t in _ALL_TOOLS if not allowed or t["name"] in allowed]

    # Agent loop
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Fallout record:\n"
                f"- transaction_id: {record.transaction_id}\n"
                f"- error_code: {record.error_code}\n"
                f"- error_message: {record.error_message}\n\n"
                f"Subscriber data:\n{json.dumps(subscriber.raw, indent=2)}\n\n"
                "Resolve this fallout by following the skill instructions."
            ),
        }
    ]

    final_message = ""
    escalated = False

    for _ in range(settings.max_tool_calls_per_run + 1):
        if guardrails.check_tool_limit(tool_call_count):
            final_message = "Tool call limit reached. Escalating to human review."
            escalated = True
            break

        ehap_response = await ehap.chat(messages=messages, system=skill.content, tools=tools)
        llm_calls += 1

        if not ehap_response.tool_calls:
            final_message = ehap_response.content
            if guardrails.check_confidence(final_message):
                escalated = True
            break

        # Execute tool calls
        tool_results: list[dict[str, Any]] = []
        for tc in ehap_response.tool_calls:
            tool_call_count += 1
            result = await _execute_tool(tc, record.transaction_id, allowed, session)
            tool_results.append({"tool_use_id": tc.id, "type": "tool_result", "content": json.dumps(result)})
            log.info("tool_executed", extra={"tool": tc.name, "run_id": str(run_id)})

        messages.append({"role": "assistant", "content": ehap_response.content or []})
        messages.append({"role": "user", "content": tool_results})

    duration_ms = int(time.monotonic() * 1000) - start_ms
    status = "escalated" if escalated else "resolved"

    result = FalloutRunResult(
        run_id=run_id,
        transaction_id=record.transaction_id,
        error_code=record.error_code,
        error_message=record.error_message,
        skill_id=skill.id,
        status=status,
        resolution_notes=final_message,
        tool_call_count=tool_call_count,
        llm_calls=llm_calls,
        duration_ms=duration_ms,
    )

    await repo.log_run(result)
    log.info("fallout_run_complete", extra={"run_id": str(run_id), "status": status, "duration_ms": duration_ms})
    return result
