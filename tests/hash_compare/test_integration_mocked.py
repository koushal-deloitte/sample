"""
Integration tests using in-process mocks (mongomock + moto).

These run without any external infrastructure and verify the full
pipeline logic end-to-end:
  - Option 2: MongoDB snapshot compare → SQS / Kafka
  - Option 1: upsert + CDC diff computation (change stream mocked)

Run with:
  pytest tests/hash_compare/test_integration_mocked.py -v
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SQS_QUEUE_NAME = "m1-member-diff"


def _load(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text())
    raw.pop("_comment", None)
    return raw


BASELINE_DOC = _load("canonical_member.json")
CHANGED_DOC = _load("canonical_member_changed.json")


# ---------------------------------------------------------------------------
# Lightweight in-memory MongoDB collection shim
# (avoids mongomock's dependency on cffi/cryptography)
# ---------------------------------------------------------------------------

class _InMemoryCollection:
    """Minimal pymongo Collection-compatible shim for testing."""

    def __init__(self):
        self._docs: list[dict] = []

    def _match(self, doc: dict, filter_: dict) -> bool:
        return all(doc.get(k) == v for k, v in filter_.items())

    def find_one(self, filter_: dict, projection: dict | None = None) -> dict | None:
        for doc in self._docs:
            if self._match(doc, filter_):
                result = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    def replace_one(self, filter: dict, replacement: dict, upsert: bool = False):
        for i, doc in enumerate(self._docs):
            if self._match(doc, filter):
                self._docs[i] = copy.deepcopy(replacement)
                return
        if upsert:
            self._docs.append(copy.deepcopy(replacement))

    def delete_many(self, filter_: dict):
        self._docs = [d for d in self._docs if not self._match(d, filter_)]


@pytest.fixture()
def mongo_col():
    """Lightweight in-memory collection for pipeline tests."""
    return _InMemoryCollection()




# ---------------------------------------------------------------------------
# Option 2 full pipeline — mongomock + SQS moto
# ---------------------------------------------------------------------------

class TestOption2FullPipeline:
    """
    End-to-end Option 2 pipeline with in-memory MongoDB shim + mocked publishers.
    Tests the read-baseline → hash-compare → classify → publish+upsert flow.
    """

    def _run(self, filename: str, col) -> object:
        from src.hash_compare.option2.pipeline import process_file

        with patch("src.hash_compare.option2.pipeline._mongo_collection", return_value=col):
            with patch("src.hash_compare.option2.pipeline._publish_sqs"):
                return process_file(
                    file_path=FIXTURES / filename,
                    mongo_uri="mongodb://localhost",
                    sqs_queue_url="http://dummy-sqs",
                )

    def test_new_member_is_classified_new(self, mongo_col):
        result = self._run("canonical_member.json", mongo_col)
        assert result.diff_result.value == "NEW"
        assert result.member_id == "MBR-00123456"
        assert mongo_col.find_one({"memberId": "MBR-00123456"}) is not None

    def test_second_identical_run_is_noop(self, mongo_col):
        self._run("canonical_member.json", mongo_col)
        result = self._run("canonical_member.json", mongo_col)
        assert result.diff_result.value == "NOOP"

    def test_changed_file_produces_changed(self, mongo_col):
        self._run("canonical_member.json", mongo_col)
        result = self._run("canonical_member_changed.json", mongo_col)
        assert result.diff_result.value == "CHANGED"
        assert "city" in result.changed_fields
        assert "coverageTier" in result.changed_fields

    def test_changed_file_updates_baseline(self, mongo_col):
        self._run("canonical_member.json", mongo_col)
        self._run("canonical_member_changed.json", mongo_col)
        stored = mongo_col.find_one({"memberId": "MBR-00123456"}, {"_id": 0})
        assert stored["city"] == "Naperville"
        assert stored["coverageTier"] == "INDIVIDUAL_SPOUSE"

    def test_noop_does_not_update_baseline(self, mongo_col):
        # Store baseline
        mongo_col.replace_one(
            {"memberId": BASELINE_DOC["memberId"], "groupId": BASELINE_DOC["groupId"]},
            copy.deepcopy(BASELINE_DOC),
            upsert=True,
        )
        original_updated_at = BASELINE_DOC.get("updatedAt")

        with patch("src.hash_compare.option2.pipeline._mongo_collection", return_value=mongo_col):
            with patch("src.hash_compare.option2.pipeline._publish_sqs") as mock_sqs:
                from src.hash_compare.option2.pipeline import process_file
                result = process_file(
                    file_path=FIXTURES / "canonical_member.json",
                    mongo_uri="mongodb://localhost",
                    sqs_queue_url="http://dummy",
                )

        assert result.diff_result.value == "NOOP"
        mock_sqs.assert_not_called()
        # Baseline should be unchanged
        stored = mongo_col.find_one({"memberId": "MBR-00123456"}, {"_id": 0})
        assert stored["updatedAt"] == original_updated_at


# ---------------------------------------------------------------------------
# Option 2 — SQS message shape
# ---------------------------------------------------------------------------

class TestOption2SqsMessageShape:
    """Verify the event published to SQS has correct fields and types."""

    def test_new_event_shape(self, mongo_col):
        from src.hash_compare.option2.pipeline import process_file

        captured_messages = []

        def fake_publish_sqs(comparison, queue_url, aws_region="us-east-1", endpoint_url=None):
            captured_messages.append(json.loads(comparison.to_json()))

        with patch("src.hash_compare.option2.pipeline._mongo_collection", return_value=mongo_col):
            with patch("src.hash_compare.option2.pipeline._publish_sqs", side_effect=fake_publish_sqs):
                process_file(
                    file_path=FIXTURES / "canonical_member.json",
                    mongo_uri="mongodb://localhost",
                    sqs_queue_url="http://dummy",
                )

        assert len(captured_messages) == 1
        event = captured_messages[0]
        assert event["diffResult"] == "NEW"
        assert event["memberId"] == "MBR-00123456"
        assert event["groupId"] == "GRP-ACME-001"
        assert len(event["incomingHash"]) == 64
        assert event["baselineHash"] is None
        assert event["changedFields"] == []
        assert event["comparisonFieldCount"] > 0
        assert isinstance(event["excludedVolatileFields"], list)

    def test_changed_event_shape(self, mongo_col):
        from src.hash_compare.option2.pipeline import process_file

        # Prime baseline
        with patch("src.hash_compare.option2.pipeline._mongo_collection", return_value=mongo_col):
            with patch("src.hash_compare.option2.pipeline._publish_sqs"):
                process_file(
                    file_path=FIXTURES / "canonical_member.json",
                    mongo_uri="mongodb://localhost",
                    sqs_queue_url="http://dummy",
                )

        captured = []

        def capture(comparison, queue_url, aws_region="us-east-1", endpoint_url=None):
            captured.append(json.loads(comparison.to_json()))

        with patch("src.hash_compare.option2.pipeline._mongo_collection", return_value=mongo_col):
            with patch("src.hash_compare.option2.pipeline._publish_sqs", side_effect=capture):
                process_file(
                    file_path=FIXTURES / "canonical_member_changed.json",
                    mongo_uri="mongodb://localhost",
                    sqs_queue_url="http://dummy",
                )

        assert len(captured) == 1
        event = captured[0]
        assert event["diffResult"] == "CHANGED"
        assert len(event["incomingHash"]) == 64
        assert len(event["baselineHash"]) == 64
        assert event["incomingHash"] != event["baselineHash"]
        assert "city" in event["changedFields"]
        assert "coverageTier" in event["changedFields"]
        # Volatile fields must NOT appear in changedFields
        assert "txnId" not in event["changedFields"]
        assert "premiumAmount" not in event["changedFields"]


# ---------------------------------------------------------------------------
# Option 1 CDC logic (mocked change stream events)
# ---------------------------------------------------------------------------

class TestOption1CdcLogic:
    """
    Tests the CDC diff computation on realistic change stream events.
    Does not require a running MongoDB replica set — events are constructed
    from the same fixtures the integration test would produce.
    """

    def test_insert_event_for_new_member(self):
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        event = {
            "operationType": "insert",
            "fullDocument": copy.deepcopy(BASELINE_DOC),
            "fullDocumentBeforeChange": None,
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result.diff_result.value == "NEW"

    def test_replace_event_with_material_change(self):
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        event = {
            "operationType": "replace",
            "fullDocument": copy.deepcopy(CHANGED_DOC),
            "fullDocumentBeforeChange": copy.deepcopy(BASELINE_DOC),
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result.diff_result.value == "CHANGED"
        assert "city" in result.changed_fields
        assert "coverageTier" in result.changed_fields

    def test_replace_event_volatile_only_is_noop(self):
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        volatile_mutation = copy.deepcopy(BASELINE_DOC)
        volatile_mutation["txnId"] = "brand-new-txn-id"
        volatile_mutation["premiumAmount"] = 99999

        event = {
            "operationType": "replace",
            "fullDocument": volatile_mutation,
            "fullDocumentBeforeChange": copy.deepcopy(BASELINE_DOC),
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result.diff_result.value == "NOOP"

    def test_delete_event_produces_remove(self):
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        event = {
            "operationType": "delete",
            "fullDocument": None,
            "fullDocumentBeforeChange": copy.deepcopy(BASELINE_DOC),
        }
        result = ChangeStreamListener._compute_diff_from_event(event)
        assert result.diff_result.value == "REMOVE"

    def test_listener_publishes_to_kafka_on_change(self):
        """
        Simulate the ChangeStreamListener processing a stream of events
        and verify it calls the Kafka producer with the correct payload.
        """
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        events_in_stream = [
            {
                "_id": {"_data": "resume-token-1"},
                "operationType": "insert",
                "fullDocument": copy.deepcopy(BASELINE_DOC),
                "fullDocumentBeforeChange": None,
            },
            {
                "_id": {"_data": "resume-token-2"},
                "operationType": "replace",
                "fullDocument": copy.deepcopy(CHANGED_DOC),
                "fullDocumentBeforeChange": copy.deepcopy(BASELINE_DOC),
            },
        ]

        listener = ChangeStreamListener(
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
            topic="m1.member.diff",
        )

        published = []

        mock_producer = MagicMock()
        mock_producer.send.side_effect = lambda topic, key, value: published.append(value)

        # Patch _make_producer and the collection watch
        class _FakeStream:
            def __init__(self, events):
                self._events = iter(events)
                self._stop = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                return self

            def __next__(self):
                if listener._stop_event.is_set():
                    raise StopIteration
                return next(self._events)

        with patch.object(listener, "_make_producer", return_value=mock_producer):
            with patch("src.hash_compare.option1.pipeline._mongo_collection") as mock_col_fn:
                mock_col = MagicMock()
                mock_col.watch.return_value = _FakeStream(events_in_stream)
                mock_col_fn.return_value = mock_col

                # Stop listener after stream exhausted (StopIteration exits the for loop)
                listener.run()

        assert len(published) == 2
        assert published[0]["diffResult"] == "NEW"
        assert published[1]["diffResult"] == "CHANGED"
        assert "city" in published[1]["changedFields"]


# ---------------------------------------------------------------------------
# Cross-option hash consistency
# ---------------------------------------------------------------------------

class TestHashConsistency:
    """Verify Option 1 and Option 2 compute identical hashes for the same document."""

    def test_same_hash_for_same_document(self):
        from src.hash_compare.hasher import compute_hash

        h1, _, _ = compute_hash(BASELINE_DOC)

        # Simulate what Option 1 CDC computes (same hasher)
        from src.hash_compare.option1.pipeline import ChangeStreamListener
        event = {
            "operationType": "insert",
            "fullDocument": copy.deepcopy(BASELINE_DOC),
            "fullDocumentBeforeChange": None,
        }
        opt1_result = ChangeStreamListener._compute_diff_from_event(event)
        assert opt1_result.incoming_hash == h1

        # Simulate what Option 2 computes
        from src.hash_compare.hasher import diff_members
        opt2_result = diff_members(incoming=BASELINE_DOC, baseline=None)
        assert opt2_result.incoming_hash == h1

    def test_changed_fields_identical_across_options(self):
        from src.hash_compare.option1.pipeline import ChangeStreamListener
        from src.hash_compare.hasher import diff_members

        event = {
            "operationType": "replace",
            "fullDocument": copy.deepcopy(CHANGED_DOC),
            "fullDocumentBeforeChange": copy.deepcopy(BASELINE_DOC),
        }
        opt1 = ChangeStreamListener._compute_diff_from_event(event)
        opt2 = diff_members(incoming=CHANGED_DOC, baseline=BASELINE_DOC)

        assert opt1.changed_fields == opt2.changed_fields
        assert opt1.incoming_hash == opt2.incoming_hash
        assert opt1.baseline_hash == opt2.baseline_hash
