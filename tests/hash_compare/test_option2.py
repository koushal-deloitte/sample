"""
Unit tests for Option 2 (MongoDB Snapshot Compare → Kafka/SQS).

All MongoDB, Kafka, and SQS calls are mocked — no infra required.
Integration tests with real containers are in test_integration.py.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.hash_compare.models import DiffResult
from src.hash_compare.option2.pipeline import (
    _fetch_baseline,
    _publish_kafka,
    _publish_sqs,
    _upsert_baseline,
    process_file,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text())
    raw.pop("_comment", None)
    return raw


BASELINE = _load("canonical_member.json")
CHANGED = _load("canonical_member_changed.json")


# ---------------------------------------------------------------------------
# _fetch_baseline
# ---------------------------------------------------------------------------

class TestFetchBaseline:
    @patch("src.hash_compare.option2.pipeline._mongo_collection")
    def test_returns_document_when_found(self, mock_col_fn):
        mock_col = MagicMock()
        mock_col.find_one.return_value = BASELINE
        mock_col_fn.return_value = mock_col

        result = _fetch_baseline("MBR-00123456", "GRP-ACME-001", "mongodb://localhost")
        assert result == BASELINE
        mock_col.find_one.assert_called_once_with(
            {"memberId": "MBR-00123456", "groupId": "GRP-ACME-001"},
            projection={"_id": 0},
        )

    @patch("src.hash_compare.option2.pipeline._mongo_collection")
    def test_returns_none_when_not_found(self, mock_col_fn):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col_fn.return_value = mock_col

        result = _fetch_baseline("MBR-MISSING", "GRP-ACME-001", "mongodb://localhost")
        assert result is None


# ---------------------------------------------------------------------------
# process_file — new member (no baseline)
# ---------------------------------------------------------------------------

class TestProcessFileNew:
    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline", return_value=None)
    def test_new_member_publishes_and_upserts(
        self, mock_fetch, mock_publish, mock_upsert
    ):
        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        assert comparison.diff_result is DiffResult.NEW
        mock_publish.assert_called_once()
        mock_upsert.assert_called_once()

    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline", return_value=None)
    def test_publish_before_upsert(self, mock_fetch, mock_publish, mock_upsert):
        """Publish must happen before upsert so a publish failure leaves baseline intact."""
        call_order = []
        mock_publish.side_effect = lambda *a, **kw: call_order.append("publish")
        mock_upsert.side_effect = lambda *a, **kw: call_order.append("upsert")

        process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        assert call_order == ["publish", "upsert"]


# ---------------------------------------------------------------------------
# process_file — changed member
# ---------------------------------------------------------------------------

class TestProcessFileChanged:
    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline", return_value=BASELINE)
    def test_changed_member_publishes_and_upserts(
        self, mock_fetch, mock_publish, mock_upsert
    ):
        comparison = process_file(
            file_path=FIXTURES / "canonical_member_changed.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        assert comparison.diff_result is DiffResult.CHANGED
        assert "city" in comparison.changed_fields
        mock_publish.assert_called_once()
        mock_upsert.assert_called_once()

    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline", return_value=BASELINE)
    def test_changed_event_payload(self, mock_fetch, mock_publish, mock_upsert):
        process_file(
            file_path=FIXTURES / "canonical_member_changed.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        comparison_arg = mock_publish.call_args.args[0]
        event = comparison_arg.to_event()
        assert event["diffResult"] == "CHANGED"
        assert "city" in event["changedFields"]


# ---------------------------------------------------------------------------
# process_file — NOOP (no publish, no upsert)
# ---------------------------------------------------------------------------

class TestProcessFileNoop:
    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline")
    def test_noop_skips_publish_and_upsert(self, mock_fetch, mock_publish, mock_upsert):
        # Same document as baseline → NOOP
        identical = copy.deepcopy(BASELINE)
        mock_fetch.return_value = identical

        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        assert comparison.diff_result is DiffResult.NOOP
        mock_publish.assert_not_called()
        mock_upsert.assert_not_called()

    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_kafka")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline")
    def test_volatile_only_change_is_noop(self, mock_fetch, mock_publish, mock_upsert):
        volatile_only = copy.deepcopy(BASELINE)
        volatile_only["txnId"] = "completely-different-txn"
        volatile_only["fileId"] = "NEW_FILE.834"
        volatile_only["premiumAmount"] = 0
        mock_fetch.return_value = volatile_only

        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri="mongodb://localhost",
            kafka_bootstrap="localhost:9092",
        )
        assert comparison.diff_result is DiffResult.NOOP
        mock_publish.assert_not_called()


# ---------------------------------------------------------------------------
# process_file — SQS transport
# ---------------------------------------------------------------------------

class TestProcessFileSqs:
    @patch("src.hash_compare.option2.pipeline._upsert_baseline")
    @patch("src.hash_compare.option2.pipeline._publish_sqs")
    @patch("src.hash_compare.option2.pipeline._fetch_baseline", return_value=None)
    def test_sqs_publish_called_on_new(self, mock_fetch, mock_sqs, mock_upsert):
        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri="mongodb://localhost",
            sqs_queue_url="http://localhost:4566/000000000000/m1-member-diff",
        )
        assert comparison.diff_result is DiffResult.NEW
        mock_sqs.assert_called_once()

    def test_no_transport_raises(self):
        with pytest.raises(ValueError, match="kafka_bootstrap or sqs_queue_url"):
            process_file(
                file_path=FIXTURES / "canonical_member.json",
                mongo_uri="mongodb://localhost",
            )


# ---------------------------------------------------------------------------
# _publish_kafka
# ---------------------------------------------------------------------------

class TestPublishKafka:
    @patch("src.hash_compare.option2.pipeline.KafkaProducer", create=True)
    def test_sends_to_topic(self, MockProducer):
        from src.hash_compare.hasher import diff_members
        comparison = diff_members(incoming=BASELINE, baseline=None)

        producer_instance = MagicMock()
        MockProducer.return_value = producer_instance

        with patch("src.hash_compare.option2.pipeline._publish_kafka") as mock_pub:
            mock_pub(comparison, "localhost:9092", "m1.member.diff")
            mock_pub.assert_called_once()


# ---------------------------------------------------------------------------
# _publish_sqs
# ---------------------------------------------------------------------------

class TestPublishSqs:
    @patch("src.hash_compare.option2.pipeline.boto3")
    def test_sends_message_with_attributes(self, mock_boto3):
        from src.hash_compare.hasher import diff_members
        comparison = diff_members(incoming=BASELINE, baseline=None)

        mock_sqs = MagicMock()
        mock_boto3.client.return_value = mock_sqs

        _publish_sqs(
            comparison,
            queue_url="http://localhost:4566/000000000000/m1-member-diff",
            endpoint_url="http://localhost:4566",
        )

        mock_sqs.send_message.assert_called_once()
        call_kwargs = mock_sqs.send_message.call_args.kwargs
        assert call_kwargs["MessageAttributes"]["diffResult"]["StringValue"] == "NEW"
        assert call_kwargs["MessageAttributes"]["memberId"]["StringValue"] == "MBR-00123456"

    @patch("src.hash_compare.option2.pipeline.boto3")
    def test_message_body_is_valid_json(self, mock_boto3):
        from src.hash_compare.hasher import diff_members
        comparison = diff_members(incoming=BASELINE, baseline=None)

        mock_sqs = MagicMock()
        mock_boto3.client.return_value = mock_sqs

        _publish_sqs(
            comparison,
            queue_url="http://localhost:4566/000000000000/m1-member-diff",
        )

        body = mock_sqs.send_message.call_args.kwargs["MessageBody"]
        parsed = json.loads(body)
        assert parsed["diffResult"] == "NEW"
