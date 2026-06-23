"""
LLM-as-judge: scores the quality of an agent reasoning trace using EHAP/Claude.

Usage:
    from tests.evals.judge import score_run
    score = await score_run(resolution_notes="...", tool_calls_summary="...", expected_outcome="resolved")
"""
import re

from src.ehap.client import EHAPClient

_JUDGE_SYSTEM = """You are an expert evaluator for an AI medical insurance fallout resolution agent.
Given an agent's resolution notes and a list of tool calls it made, score the quality from 1 to 5.

Scoring rubric:
5 — Correct resolution, clear reasoning, minimal tool calls
4 — Correct resolution, minor inefficiency
3 — Correct outcome but unclear reasoning or excessive tool calls
2 — Incorrect outcome or unnecessary escalation
1 — Harmful, confused, or hallucinatory reasoning

Respond with ONLY a JSON object: {"score": <1-5>, "rationale": "<one sentence>"}
"""


async def score_run(
    resolution_notes: str,
    tool_calls_summary: str,
    expected_outcome: str,
    ehap: EHAPClient | None = None,
) -> dict:
    close_after = ehap is None
    if ehap is None:
        ehap = EHAPClient()

    user_msg = (
        f"Expected outcome: {expected_outcome}\n\n"
        f"Agent resolution notes:\n{resolution_notes}\n\n"
        f"Tools called:\n{tool_calls_summary}"
    )
    response = await ehap.chat(
        messages=[{"role": "user", "content": user_msg}],
        system=_JUDGE_SYSTEM,
    )
    if close_after:
        await ehap.close()

    import json
    try:
        return json.loads(response.content)
    except Exception:
        match = re.search(r'"score"\s*:\s*(\d)', response.content)
        score = int(match.group(1)) if match else 0
        return {"score": score, "rationale": response.content}
