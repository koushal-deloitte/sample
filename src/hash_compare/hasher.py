"""
SHA-256 hash computation for canonical member comparison.

Volatile fields are excluded per the M1 spec "Volatile Fields Summary" section.
These fields change with every transaction regardless of actual member data
changes and must not participate in the diff gate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import DiffResult, HashComparison

# Canonical volatile fields per spec §Volatile Fields Summary
# plus all fields marked VOLATILE: YES in section tables.
VOLATILE_FIELDS: frozenset[str] = frozenset({
    # §8 Transaction metadata
    "txnId",
    "fileId",
    "sourceFileLineNumber",
    "batchId",
    "attempt",
    "submittedAt",
    "processedAt",
    "committedAt",
    # §5 Coverage — rate changes don't reflect member changes
    "premiumAmount",
    "employerContribution",
    # §10 Enrichment timestamps
    "enrichedAt",
    "nameVerifiedAt",
    "addressVerifiedAt",
    "planDetailsLoadedAt",
    "networkLoadedAt",
    "employerDataLoadedAt",
    "premiumCalculatedAt",
    "enrichmentSnapshot",
    # §12 Audit — pipeline lifecycle fields
    "createdAt",
    "updatedAt",
    "version",
    "pipelineStage",
    "txnState",
    "mfAckReceivedAt",
    "odsWrittenAt",
    # Internal JSON comment field (fixture-only)
    "_comment",
})

# Diff result fields written by the pipeline itself must not feed the hash
_DIFF_OUTPUT_FIELDS: frozenset[str] = frozenset({
    "diffResult",
    "changedFields",
    "baselineHash",
    "incomingHash",
    "diffedAt",
    "baselineSnapshotPath",
    "baselineVersionId",
    "comparisonFieldCount",
    "excludedVolatileFields",
})

_EXCLUDED: frozenset[str] = VOLATILE_FIELDS | _DIFF_OUTPUT_FIELDS


def _stable_serialize(value: Any) -> Any:
    """Recursively sort dicts and lists-of-dicts for deterministic serialization."""
    if isinstance(value, dict):
        return {k: _stable_serialize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        items = [_stable_serialize(i) for i in value]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except TypeError:
            return items
    return value


def compute_hash(member: dict[str, Any]) -> tuple[str, list[str], int]:
    """Return (sha256_hex, excluded_field_names, comparison_field_count)."""
    excluded = sorted(k for k in member if k in _EXCLUDED)
    comparable = {k: v for k, v in member.items() if k not in _EXCLUDED}
    payload = json.dumps(_stable_serialize(comparable), separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return digest, excluded, len(comparable)


def diff_members(
    incoming: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> HashComparison:
    """
    Compare an incoming canonical member against a stored baseline.

    Returns a HashComparison with diff_result in {NEW, CHANGED, NOOP, REMOVE}.
    REMOVE is not produced here — callers signal REMOVE by passing incoming=None
    (not yet wired; REMOVE originates from the source file txnType='termination').
    """
    incoming_hash, excluded, field_count = compute_hash(incoming)

    if baseline is None:
        return HashComparison(
            member_id=incoming["memberId"],
            group_id=incoming["groupId"],
            diff_result=DiffResult.NEW,
            incoming_hash=incoming_hash,
            baseline_hash=None,
            changed_fields=[],
            comparison_field_count=field_count,
            excluded_volatile_fields=excluded,
        )

    baseline_hash, _, _ = compute_hash(baseline)

    if incoming_hash == baseline_hash:
        return HashComparison(
            member_id=incoming["memberId"],
            group_id=incoming["groupId"],
            diff_result=DiffResult.NOOP,
            incoming_hash=incoming_hash,
            baseline_hash=baseline_hash,
            changed_fields=[],
            comparison_field_count=field_count,
            excluded_volatile_fields=excluded,
        )

    # Identify which non-volatile fields actually changed
    changed = [
        k
        for k in set(incoming) | set(baseline)
        if k not in _EXCLUDED and incoming.get(k) != baseline.get(k)
    ]

    return HashComparison(
        member_id=incoming["memberId"],
        group_id=incoming["groupId"],
        diff_result=DiffResult.CHANGED,
        incoming_hash=incoming_hash,
        baseline_hash=baseline_hash,
        changed_fields=sorted(changed),
        comparison_field_count=field_count,
        excluded_volatile_fields=excluded,
    )
