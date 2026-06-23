"""
EHAP client — thin async HTTP wrapper around the internal enterprise Claude platform.

The actual request/response schema for EHAP is to be confirmed with the EHAP team.
This client assumes a messages-style API similar to Anthropic's Messages API but
hosted at an internal endpoint with a custom auth header.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import settings

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class EHAPResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


class EHAPClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ehap_base_url,
            headers={
                "Authorization": f"Bearer {settings.ehap_api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.ehap_timeout_seconds,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> EHAPResponse:
        payload: dict[str, Any] = {
            "model": settings.ehap_model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        log.debug("ehap_request", extra={"message_count": len(messages), "has_tools": bool(tools)})

        # STUB: Replace /v1/messages with the actual EHAP endpoint path once confirmed
        response = await self._client.post("/v1/messages", json=payload)
        response.raise_for_status()
        return self._parse(response.json())

    def _parse(self, body: dict[str, Any]) -> EHAPResponse:
        # Normalise from an Anthropic-style response envelope
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for block in body.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        input=block.get("input", {}),
                    )
                )

        return EHAPResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=body.get("stop_reason", "end_turn"),
        )

    async def close(self) -> None:
        await self._client.aclose()
