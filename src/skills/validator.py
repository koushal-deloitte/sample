import frontmatter

from src.models.skill import SkillCreate

_REQUIRED_FRONTMATTER = {"name", "error_codes"}
_PHI_PATTERNS = ["log phi", "log ssn", "log dob", "print ssn", "expose phi", "store ssn"]


class SkillValidationError(ValueError):
    pass


def parse_and_validate(raw_markdown: str) -> SkillCreate:
    """Parse Markdown with YAML front-matter into a SkillCreate, raising SkillValidationError on failure."""
    try:
        post = frontmatter.loads(raw_markdown)
    except Exception as exc:
        raise SkillValidationError(f"Failed to parse skill Markdown: {exc}") from exc

    metadata = post.metadata
    missing = _REQUIRED_FRONTMATTER - set(metadata.keys())
    if missing:
        raise SkillValidationError(f"Skill is missing required front-matter fields: {missing}")

    error_codes = metadata.get("error_codes", [])
    if isinstance(error_codes, str):
        error_codes = [c.strip() for c in error_codes.split(",") if c.strip()]
    if not error_codes:
        raise SkillValidationError("error_codes must not be empty")

    content_lower = post.content.lower()
    for pattern in _PHI_PATTERNS:
        if pattern in content_lower:
            raise SkillValidationError(f"Skill content appears to expose PHI (found pattern: '{pattern}')")

    return SkillCreate(
        name=str(metadata["name"]),
        description=str(metadata.get("description", "")),
        error_codes=[str(c) for c in error_codes],
        allowed_tools=list(metadata.get("allowed_tools", [])),
        content=raw_markdown,
    )


def build_skill_template(error_code: str, description: str = "") -> str:
    """Return a Markdown template for a new skill."""
    return f"""---
name: {error_code} Handler
description: {description or f'Handles fallout type {error_code}'}
error_codes:
  - {error_code}
allowed_tools:
  - call_api
  - escalate
---

## Objective
Resolve fallouts with error code `{error_code}`.

## Steps
1. Review the subscriber canonical data provided.
2. Identify the root cause of the fallout.
3. Call the appropriate API to resolve the issue.
4. If resolution is not possible, escalate to human review.

## Escalation Criteria
- When the subscriber data is incomplete or contradictory.
- When the external API returns an error that cannot be retried.
"""
