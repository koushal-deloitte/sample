"""Unit tests for the hasher module — no external dependencies."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.hash_compare.hasher import (
    VOLATILE_FIELDS,
    _EXCLUDED,
    compute_hash,
    diff_members,
)
from src.hash_compare.models import DiffResult

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def baseline() -> dict:
    raw = json.loads((FIXTURES / "canonical_member.json").read_text())
    raw.pop("_comment", None)
    return raw


@pytest.fixture()
def changed() -> dict:
    raw = json.loads((FIXTURES / "canonical_member_changed.json").read_text())
    raw.pop("_comment", None)
    return raw


# ---------------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------------

class TestComputeHash:
    def test_returns_64_char_hex(self, baseline):
        h, _, _ = compute_hash(baseline)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self, baseline):
        h1, _, _ = compute_hash(baseline)
        h2, _, _ = compute_hash(baseline)
        assert h1 == h2

    def test_volatile_field_change_does_not_affect_hash(self, baseline):
        h1, _, _ = compute_hash(baseline)
        mutated = copy.deepcopy(baseline)
        mutated["txnId"] = "new-uuid-value"
        mutated["fileId"] = "DIFFERENT_FILE.834"
        mutated["premiumAmount"] = 99999
        mutated["batchId"] = "BATCH-NEW"
        h2, _, _ = compute_hash(mutated)
        assert h1 == h2, "Volatile field changes must not alter the comparison hash"

    def test_non_volatile_field_change_changes_hash(self, baseline):
        h1, _, _ = compute_hash(baseline)
        mutated = copy.deepcopy(baseline)
        mutated["city"] = "Springfield"
        h2, _, _ = compute_hash(mutated)
        assert h1 != h2

    def test_excluded_fields_reported(self, baseline):
        _, excluded, _ = compute_hash(baseline)
        # All excluded keys present in the fixture should be reported
        present_volatile = [k for k in baseline if k in _EXCLUDED]
        for k in present_volatile:
            assert k in excluded

    def test_comparison_field_count(self, baseline):
        _, _, count = compute_hash(baseline)
        expected = len([k for k in baseline if k not in _EXCLUDED])
        assert count == expected

    def test_field_order_independent(self, baseline):
        shuffled = dict(sorted(baseline.items(), reverse=True))
        h1, _, _ = compute_hash(baseline)
        h2, _, _ = compute_hash(shuffled)
        assert h1 == h2, "Hash must be order-independent"

    def test_none_values_handled(self, baseline):
        """None values in non-volatile fields are part of the hash."""
        h1, _, _ = compute_hash(baseline)
        mutated = copy.deepcopy(baseline)
        mutated["termDate"] = "2024-12-31"  # was None
        h2, _, _ = compute_hash(mutated)
        assert h1 != h2


# ---------------------------------------------------------------------------
# diff_members
# ---------------------------------------------------------------------------

class TestDiffMembers:
    def test_new_when_no_baseline(self, baseline):
        result = diff_members(incoming=baseline, baseline=None)
        assert result.diff_result is DiffResult.NEW
        assert result.baseline_hash is None
        assert result.changed_fields == []
        assert result.member_id == "MBR-00123456"
        assert result.group_id == "GRP-ACME-001"

    def test_noop_on_identical(self, baseline):
        baseline_copy = copy.deepcopy(baseline)
        # Mutate only volatile fields — hash must be the same
        baseline_copy["txnId"] = "different-txn-id"
        baseline_copy["submittedAt"] = "2099-01-01T00:00:00Z"
        result = diff_members(incoming=baseline, baseline=baseline_copy)
        assert result.diff_result is DiffResult.NOOP
        assert result.changed_fields == []

    def test_changed_detects_address_change(self, baseline, changed):
        result = diff_members(incoming=changed, baseline=baseline)
        assert result.diff_result is DiffResult.CHANGED
        assert "city" in result.changed_fields
        assert "zip" in result.changed_fields
        assert "county" in result.changed_fields

    def test_changed_detects_coverage_tier_change(self, baseline, changed):
        result = diff_members(incoming=changed, baseline=baseline)
        assert "coverageTier" in result.changed_fields

    def test_volatile_only_changes_not_in_changed_fields(self, baseline):
        mutated = copy.deepcopy(baseline)
        mutated["txnId"] = "new-txn"
        mutated["premiumAmount"] = 99999
        result = diff_members(incoming=mutated, baseline=baseline)
        assert result.diff_result is DiffResult.NOOP
        assert "txnId" not in result.changed_fields
        assert "premiumAmount" not in result.changed_fields

    def test_changed_fields_sorted(self, baseline, changed):
        result = diff_members(incoming=changed, baseline=baseline)
        assert result.changed_fields == sorted(result.changed_fields)

    def test_hashes_populated_on_changed(self, baseline, changed):
        result = diff_members(incoming=changed, baseline=baseline)
        assert result.incoming_hash and len(result.incoming_hash) == 64
        assert result.baseline_hash and len(result.baseline_hash) == 64
        assert result.incoming_hash != result.baseline_hash

    def test_comparison_field_count_consistent(self, baseline):
        result = diff_members(incoming=baseline, baseline=None)
        _, _, expected_count = compute_hash(baseline)
        assert result.comparison_field_count == expected_count

    def test_to_event_serializable(self, baseline, changed):
        result = diff_members(incoming=changed, baseline=baseline)
        event = result.to_event()
        assert event["diffResult"] == "CHANGED"
        assert event["memberId"] == "MBR-00123456"
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert parsed["diffResult"] == "CHANGED"


# ---------------------------------------------------------------------------
# Volatile fields inventory
# ---------------------------------------------------------------------------

class TestVolatileFieldInventory:
    def test_spec_volatile_fields_all_present(self):
        """The 10 fields from the spec §Volatile Fields Summary must be excluded."""
        spec_volatile = {
            "txnId", "fileId", "sourceFileLineNumber", "batchId", "attempt",
            "submittedAt", "processedAt", "committedAt",
            "premiumAmount", "employerContribution",
        }
        assert spec_volatile.issubset(VOLATILE_FIELDS)

    def test_phi_fields_not_in_volatile(self):
        """PHI fields (tokenized) should participate in the hash — they are stable."""
        phi_stable = {"firstName", "lastName", "dateOfBirth", "ssn"}
        overlap = phi_stable & VOLATILE_FIELDS
        assert not overlap, f"PHI fields should not be volatile: {overlap}"
