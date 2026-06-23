"""Database read/write tool for the fallout agent."""
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

TOOL_DEFINITION = {
    "name": "query_db",
    "description": (
        "Execute a read-only SQL SELECT query or a write query (INSERT/UPDATE) against the operational database. "
        "Write operations require confirm=true."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "The SQL query to execute"},
            "params": {"type": "object", "description": "Optional named parameters for the query"},
            "confirm": {
                "type": "boolean",
                "description": "Must be true for INSERT/UPDATE/DELETE operations (write guardrail)",
            },
        },
        "required": ["sql"],
    },
}

_WRITE_KEYWORDS = {"insert", "update", "delete", "drop", "truncate", "alter"}


def _is_write_query(sql: str) -> bool:
    first_word = sql.strip().split()[0].lower() if sql.strip() else ""
    return first_word in _WRITE_KEYWORDS


async def execute(input: dict[str, Any], session: AsyncSession, dry_run: bool = False) -> dict[str, Any]:
    sql = input["sql"]
    params = input.get("params", {})
    confirm = input.get("confirm", False)

    if _is_write_query(sql) and not confirm:
        log.warning("db_write_blocked_no_confirm", extra={"sql_preview": sql[:80]})
        return {"error": "Write query requires confirm=true before execution."}

    if dry_run:
        log.info("dry_run_db_query", extra={"sql_preview": sql[:80]})
        return {"dry_run": True, "sql": sql, "params": params}

    log.info("db_query", extra={"sql_preview": sql[:80]})
    result = await session.execute(text(sql), params)
    if _is_write_query(sql):
        await session.commit()
        return {"rows_affected": result.rowcount}
    rows = [dict(row._mapping) for row in result]
    return {"rows": rows, "count": len(rows)}
