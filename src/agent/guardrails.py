import logging
import re
from typing import Any

from src.config import settings

log = logging.getLogger(__name__)

_UNCERTAINTY_PATTERNS = [
    r"\bi('m| am) not sure\b",
    r"\bi('m| am) uncertain\b",
    r"\bcannot (determine|confirm|verify)\b",
    r"\bunable to (resolve|determine|confirm)\b",
    r"\bneed (more|additional) (information|data|clarification)\b",
    r"\binsufficient (data|information)\b",
]
_UNCERTAINTY_RE = re.compile("|".join(_UNCERTAINTY_PATTERNS), re.IGNORECASE)


def check_tool_limit(tool_call_count: int) -> bool:
    """Returns True if the agent has exceeded the allowed tool call count."""
    exceeded = tool_call_count >= settings.max_tool_calls_per_run
    if exceeded:
        log.warning("tool_call_limit_exceeded", extra={"count": tool_call_count, "limit": settings.max_tool_calls_per_run})
    return exceeded


def check_confidence(agent_response: str) -> bool:
    """Returns True if the response contains uncertainty markers → should escalate."""
    if not settings.confidence_gate_enabled:
        return False
    matched = bool(_UNCERTAINTY_RE.search(agent_response))
    if matched:
        log.info("confidence_gate_triggered", extra={"response_preview": agent_response[:120]})
    return matched


def is_tool_allowed(tool_name: str, allowed_tools: list[str]) -> bool:
    """Returns True if the tool is in the skill's allowlist (or allowlist is empty = all allowed)."""
    if not allowed_tools:
        return True
    allowed = tool_name in allowed_tools
    if not allowed:
        log.warning("tool_not_allowed", extra={"tool": tool_name, "allowed": allowed_tools})
    return allowed
