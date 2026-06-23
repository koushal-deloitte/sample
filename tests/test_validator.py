import pytest

from src.skills.validator import SkillValidationError, parse_and_validate

_VALID_SKILL = """---
name: MISSING_NPI Handler
description: Handles MISSING_NPI fallout
error_codes:
  - MISSING_NPI
allowed_tools:
  - call_api
---

## Objective
Look up the NPI and resolve the enrollment.
"""

_MISSING_NAME = """---
error_codes:
  - MISSING_NPI
---

Content here.
"""

_PHI_LEAK = """---
name: Bad Skill
error_codes:
  - SOME_CODE
---

Step 1: log ssn to the audit system.
"""


def test_valid_skill_parses():
    skill = parse_and_validate(_VALID_SKILL)
    assert skill.name == "MISSING_NPI Handler"
    assert "MISSING_NPI" in skill.error_codes
    assert skill.allowed_tools == ["call_api"]


def test_missing_name_raises():
    with pytest.raises(SkillValidationError, match="name"):
        parse_and_validate(_MISSING_NAME)


def test_phi_pattern_raises():
    with pytest.raises(SkillValidationError, match="PHI"):
        parse_and_validate(_PHI_LEAK)


def test_empty_error_codes_raises():
    bad = """---
name: Empty codes
error_codes: []
---
Content.
"""
    with pytest.raises(SkillValidationError):
        parse_and_validate(bad)
