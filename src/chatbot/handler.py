"""Chatbot agent loop — skill CRUD via natural language."""
import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ehap.client import EHAPClient
from src.models.skill import ChatMessage, Skill, SkillCreate, SkillUpdate
from src.skills.repository import SkillRepository
from src.skills.validator import SkillValidationError, build_skill_template, parse_and_validate

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a skill management assistant for the Fallout Agent system.
You help business users create, view, edit, and deactivate fallout handling skill files.

Each skill file is a Markdown document with YAML front-matter that contains:
- name: human-readable skill name
- description: brief description
- error_codes: list of error codes this skill handles
- allowed_tools: list of tools the agent may use (call_api, query_db, escalate)

Your behaviour:
1. For CREATE: ask the user to describe the new fallout scenario, then draft a skill in Markdown and SHOW it to the user before saving. Wait for their explicit "confirm" or "yes" before calling create_skill.
2. For EDIT: retrieve the existing skill, show the proposed changes as a diff, and wait for confirmation before calling update_skill.
3. For DELETE/DEACTIVATE: always confirm before calling deactivate_skill.
4. For READ/EXPLAIN: call get_skill or list_skills and summarise clearly.
5. Never expose raw PHI fields in your responses.
"""

_TOOL_DEFINITIONS = [
    {
        "name": "list_skills",
        "description": "List all active skill files.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_skill",
        "description": "Get a skill by its name or error code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Skill name or error code to look up"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_skill",
        "description": "Save a new skill. Only call after user has confirmed the draft.",
        "input_schema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string", "description": "Full Markdown content of the skill including front-matter"},
            },
            "required": ["markdown"],
        },
    },
    {
        "name": "update_skill",
        "description": "Update an existing skill. Only call after user has confirmed the changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "UUID of the skill to update"},
                "markdown": {"type": "string", "description": "Updated full Markdown content"},
            },
            "required": ["skill_id", "markdown"],
        },
    },
    {
        "name": "deactivate_skill",
        "description": "Deactivate (soft-delete) a skill. Only call after user has confirmed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "UUID of the skill to deactivate"},
            },
            "required": ["skill_id"],
        },
    },
]


async def _execute_tool(tool_name: str, tool_input: dict[str, Any], session: AsyncSession) -> str:
    repo = SkillRepository(session)

    if tool_name == "list_skills":
        skills = await repo.list_active()
        if not skills:
            return "No active skills found."
        lines = [f"- **{s.name}** (codes: {', '.join(s.error_codes)}) — ID: {s.id}" for s in skills]
        return "\n".join(lines)

    if tool_name == "get_skill":
        query = tool_input["query"]
        skill = await repo.get_by_error_code(query)
        if not skill:
            skills = await repo.list_active()
            skill = next((s for s in skills if query.lower() in s.name.lower()), None)
        if not skill:
            return f"No skill found matching '{query}'."
        return f"**{skill.name}** (ID: {skill.id})\nError codes: {skill.error_codes}\n\n```markdown\n{skill.content}\n```"

    if tool_name == "create_skill":
        try:
            skill_data = parse_and_validate(tool_input["markdown"])
        except SkillValidationError as exc:
            return f"Skill validation failed: {exc}"
        skill = await repo.create(skill_data, created_by="chatbot")
        return f"Skill **{skill.name}** created with ID `{skill.id}`."

    if tool_name == "update_skill":
        try:
            skill_id = uuid.UUID(tool_input["skill_id"])
        except ValueError:
            return "Invalid skill_id — must be a UUID."
        try:
            skill_data = parse_and_validate(tool_input["markdown"])
        except SkillValidationError as exc:
            return f"Skill validation failed: {exc}"
        updated = await repo.update(
            skill_id,
            SkillUpdate(
                name=skill_data.name,
                description=skill_data.description,
                error_codes=skill_data.error_codes,
                content=skill_data.content,
                allowed_tools=skill_data.allowed_tools,
                updated_by="chatbot",
            ),
        )
        if not updated:
            return f"Skill `{skill_id}` not found."
        return f"Skill **{updated.name}** updated to version {updated.version}."

    if tool_name == "deactivate_skill":
        try:
            skill_id = uuid.UUID(tool_input["skill_id"])
        except ValueError:
            return "Invalid skill_id — must be a UUID."
        success = await repo.deactivate(skill_id)
        return f"Skill `{skill_id}` deactivated." if success else f"Skill `{skill_id}` not found."

    return f"Unknown tool: {tool_name}"


async def handle_message(
    message: str,
    history: list[dict[str, Any]],
    ehap: EHAPClient,
    session: AsyncSession,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Run one turn of the chatbot agent loop.
    Returns (assistant_reply, updated_history).
    """
    history = list(history)
    history.append({"role": "user", "content": message})

    for _ in range(10):  # max tool call iterations per user turn
        response = await ehap.chat(messages=history, system=_SYSTEM_PROMPT, tools=_TOOL_DEFINITIONS)

        if not response.tool_calls:
            history.append({"role": "assistant", "content": response.content})
            return response.content, history

        tool_results = []
        for tc in response.tool_calls:
            log.info("chatbot_tool_call", extra={"tool": tc.name})
            result_text = await _execute_tool(tc.name, tc.input, session)
            tool_results.append({"tool_use_id": tc.id, "type": "tool_result", "content": result_text})

        history.append({"role": "assistant", "content": response.content or []})
        history.append({"role": "user", "content": tool_results})

    return "I was unable to complete the request within the allowed steps.", history
