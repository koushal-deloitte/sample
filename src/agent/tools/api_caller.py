"""Generic REST/SOAP API caller tool."""
import logging
from typing import Any

import httpx

from src.config import settings

log = logging.getLogger(__name__)

TOOL_DEFINITION = {
    "name": "call_api",
    "description": (
        "Make an HTTP request to an external REST or SOAP API. "
        "Use this to retrieve data or trigger actions on external systems."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "url": {"type": "string", "description": "Full URL of the endpoint"},
            "headers": {"type": "object", "description": "Optional HTTP headers"},
            "body": {"type": "object", "description": "Optional JSON request body"},
            "confirm": {
                "type": "boolean",
                "description": "Must be true for POST/PUT/PATCH/DELETE to execute (write guardrail)",
            },
        },
        "required": ["method", "url"],
    },
}


async def execute(input: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    method = input["method"].upper()
    url = input["url"]
    headers = input.get("headers", {})
    body = input.get("body")
    confirm = input.get("confirm", False)

    if method in {"POST", "PUT", "PATCH", "DELETE"} and not confirm:
        log.warning("api_call_blocked_no_confirm", extra={"method": method, "url": url})
        return {"error": f"{method} to {url} requires confirm=true before execution."}

    if dry_run:
        log.info("dry_run_api_call", extra={"method": method, "url": url})
        return {"dry_run": True, "method": method, "url": url, "body": body}

    log.info("api_call", extra={"method": method, "url": url})
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, headers=headers, json=body)
        return {"status_code": response.status_code, "body": response.json() if response.content else {}}
