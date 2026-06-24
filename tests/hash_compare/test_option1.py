"""
Unit tests for Option 1 (MongoDB CDC → Kafka).

All MongoDB and Kafka calls are mocked — no infra required.
Integration tests that use real containers are in test_integration.py.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.hash_compare.models import DiffResult
from src.hash_compare.option1.pipeline import (
    ChangeStreamListener,
    process_file,
    upsert_member,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text())
    raw.pop("_comment", None)
    return raw


BASELINE = _load("canonical_member.json")
CHANGED = _load("canonical_member_changed.json")


# ---------------------------------------------------------------------------
# upsert_member
# ---------------------------------------------------------------------------

class TestUpsertMember:
    @patch("src.hash_compare.option1.pipeline._mongo_collection")
    def test_calls_replace_one_with_upsert(self, mock_col_fn):
        mock_col = MagicMock()
        mock_col_fn.return_value = mock_col

        upsert_member(BASELINE, mongo_uri="mongodb://localhost:27017")

        mock_col.replace_one.assert_called_once()
        call_kwargs = mock_col.replace_one.call_args
        assert call_kwargs.kwargs.get("upsert") is True or call_kwargs.args[2] is True or \
               (len(call_kwargs.args) >= 3 and call_kwargs.args[2] is True) or \
               call_kwargs.kwargs.get("upsert") is True

    @patch("src.hash_compare.option1.pipeline._mongo_collection")
    def test_filter_uses_member_and_group_id(self, mock_col_fn):
        mock_col = MagicMock()
        mock_col_fn.return_value = mock_col

        upsert_member(BASELINE, mongo_uri="mongodb://localhost:27017")

        # replace_one is called with keyword arguments
        call_kwargs = mock_col.replace_one.call_args.kwargs
        filter_arg = call_kwargs.get("filter") or mock_col.replace_one.call_args.args[0]
        assert filter_arg["memberId"] == "MBR-00123456"
        assert filter_arg["groupId"] == "GRP-ACME-001"


# ---------------------------------------------------------------------------
# ChangeStreamListener._compute_diff_from_event
# ---------------------------------------------------------------------------

class TestComputeDiffFromEvent:
    def test_insert_event_returns_new(self):
        event = {
            "operationType": "insert",
            "fullDocument": BASELINE,
            "fullDocumentBeforeChange": None,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is not None
        assert result.diff_result is DiffResult.NEW

    def test_replace_event_with_preimage_returns_changed(self):
        event = {
            "operationType": "replace",
            "fullDocument": CHANGED,
            "fullDocumentBeforeChange": BASELINE,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is not None
        assert result.diff_result is DiffResult.CHANGED
        assert "city" in result.changed_fields

    def test_replace_event_noop_on_volatile_only_change(self):
        mutated = copy.deepcopy(BASELINE)
        mutated["txnId"] = "new-txn-id"
        mutated["premiumAmount"] = 99999
        event = {
            "operationType": "replace",
            "fullDocument": mutated,
            "fullDocumentBeforeChange": BASELINE,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is not None
        assert result.diff_result is DiffResult.NOOP

    def test_delete_event_returns_remove(self):
        event = {
            "operationType": "delete",
            "fullDocument": None,
            "fullDocumentBeforeChange": BASELINE,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is not None
        assert result.diff_result is DiffResult.REMOVE

    def test_delete_without_preimage_returns_none(self):
        event = {
            "operationType": "delete",
            "fullDocument": None,
            "fullDocumentBeforeChange": None,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is None

    def test_missing_full_document_returns_none(self):
        event = {
            "operationType": "replace",
            "fullDocument": None,
            "fullDocumentBeforeChange": None,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result is None


# ---------------------------------------------------------------------------
# process_file
# ---------------------------------------------------------------------------

class TestProcessFile:
    @patch("src.hash_compare.option1.pipeline.upsert_member")
    def test_loads_json_and_upserts(self, mock_upsert, tmp_path):
        fixture = FIXTURES / "canonical_member.json"
        process_file(
            file_path=fixture,
            mongo_uri="mongodb://localhost:27017",
            kafka_bootstrap="localhost:9092",
        )
        mock_upsert.assert_called_once()
        call_member = mock_upsert.call_args.args[0]
        assert call_member["memberId"] == "MBR-00123456"

    @patch("src.hash_compare.option1.pipeline.upsert_member")
    def test_invalid_json_raises(self, mock_upsert, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json}")
        with pytest.raises(Exception):
            process_file(
                file_path=bad,
                mongo_uri="mongodb://localhost:27017",
                kafka_bootstrap="localhost:9092",
            )
        mock_upsert.assert_not_called()
