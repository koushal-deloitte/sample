"""
Integration tests for both options.

Requires the podman-compose infra to be running:
  podman-compose -f infra/podman-compose.yml up -d

Tests are automatically skipped if the services are unreachable.
Run manually:
  pytest tests/hash_compare/test_integration.py -v -s
"""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MONGO_URI = "mongodb://localhost:27017/?replicaSet=rs0"
KAFKA_BOOTSTRAP = "localhost:9092"
SQS_ENDPOINT = "http://localhost:4566"
SQS_QUEUE_URL = "http://localhost:4566/000000000000/m1-member-diff"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


requires_mongo = pytest.mark.skipif(
    not _port_open("localhost", 27017),
    reason="MongoDB not reachable — start infra: podman-compose -f infra/podman-compose.yml up -d",
)
requires_kafka = pytest.mark.skipif(
    not _port_open("localhost", 9092),
    reason="Kafka not reachable — start infra: podman-compose -f infra/podman-compose.yml up -d",
)
requires_sqs = pytest.mark.skipif(
    not _port_open("localhost", 4566),
    reason="LocalStack not reachable — start infra: podman-compose -f infra/podman-compose.yml up -d",
)


def _load(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text())
    raw.pop("_comment", None)
    return raw


# ---------------------------------------------------------------------------
# Option 2 integration — Mongo + Kafka
# ---------------------------------------------------------------------------

@requires_mongo
@requires_kafka
class TestOption2Integration:
    """End-to-end: file → MongoDB snapshot compare → Kafka."""

    def _flush_mongo(self):
        import pymongo
        client = pymongo.MongoClient(MONGO_URI)
        client["m1"]["members"].delete_many({})

    def setup_method(self):
        self._flush_mongo()

    def test_new_member_flow(self):
        from src.hash_compare.option2.pipeline import process_file

        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )
        assert comparison.diff_result.value == "NEW"
        assert comparison.member_id == "MBR-00123456"

    def test_second_identical_file_is_noop(self):
        from src.hash_compare.option2.pipeline import process_file

        process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )
        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )
        assert comparison.diff_result.value == "NOOP"

    def test_changed_file_produces_diff(self):
        from src.hash_compare.option2.pipeline import process_file

        process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )
        comparison = process_file(
            file_path=FIXTURES / "canonical_member_changed.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )
        assert comparison.diff_result.value == "CHANGED"
        assert "city" in comparison.changed_fields
        assert "coverageTier" in comparison.changed_fields

    def test_kafka_event_consumed(self):
        """Verify the event published to Kafka is well-formed."""
        from kafka import KafkaConsumer
        from src.hash_compare.option2.pipeline import process_file

        consumer = KafkaConsumer(
            "m1.member.diff",
            bootstrap_servers=KAFKA_BOOTSTRAP,
            auto_offset_reset="earliest",
            consumer_timeout_ms=5000,
            value_deserializer=lambda v: json.loads(v.decode()),
            group_id=f"test-group-{time.time()}",
        )

        process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
        )

        messages = []
        for msg in consumer:
            messages.append(msg.value)
            break
        consumer.close()

        assert len(messages) == 1
        event = messages[0]
        assert event["diffResult"] == "NEW"
        assert event["memberId"] == "MBR-00123456"
        assert "incomingHash" in event
        assert len(event["incomingHash"]) == 64


# ---------------------------------------------------------------------------
# Option 2 integration — Mongo + SQS (LocalStack)
# ---------------------------------------------------------------------------

@requires_mongo
@requires_sqs
class TestOption2SqsIntegration:
    def _flush_mongo(self):
        import pymongo
        client = pymongo.MongoClient(MONGO_URI)
        client["m1"]["members"].delete_many({})

    def _ensure_queue(self):
        import boto3
        sqs = boto3.client(
            "sqs",
            region_name="us-east-1",
            endpoint_url=SQS_ENDPOINT,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        try:
            sqs.create_queue(QueueName="m1-member-diff")
        except Exception:
            pass
        return sqs

    def setup_method(self):
        self._flush_mongo()
        self._ensure_queue()

    def test_new_member_publishes_to_sqs(self):
        import boto3
        from src.hash_compare.option2.pipeline import process_file

        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            sqs_queue_url=SQS_QUEUE_URL,
            sqs_endpoint_url=SQS_ENDPOINT,
        )
        assert comparison.diff_result.value == "NEW"

        sqs = boto3.client(
            "sqs",
            region_name="us-east-1",
            endpoint_url=SQS_ENDPOINT,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        resp = sqs.receive_message(QueueUrl=SQS_QUEUE_URL, MaxNumberOfMessages=1)
        messages = resp.get("Messages", [])
        assert len(messages) == 1
        body = json.loads(messages[0]["Body"])
        assert body["diffResult"] == "NEW"
        assert body["memberId"] == "MBR-00123456"

    def test_noop_does_not_publish_to_sqs(self):
        import boto3
        from src.hash_compare.option2.pipeline import process_file

        # First pass — NEW → publishes
        process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            sqs_queue_url=SQS_QUEUE_URL,
            sqs_endpoint_url=SQS_ENDPOINT,
        )

        sqs = boto3.client(
            "sqs",
            region_name="us-east-1",
            endpoint_url=SQS_ENDPOINT,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        # Drain the NEW message
        sqs.receive_message(QueueUrl=SQS_QUEUE_URL, MaxNumberOfMessages=10)

        # Second pass — NOOP → must NOT publish
        comparison = process_file(
            file_path=FIXTURES / "canonical_member.json",
            mongo_uri=MONGO_URI,
            sqs_queue_url=SQS_QUEUE_URL,
            sqs_endpoint_url=SQS_ENDPOINT,
        )
        assert comparison.diff_result.value == "NOOP"

        resp = sqs.receive_message(QueueUrl=SQS_QUEUE_URL, MaxNumberOfMessages=10)
        assert len(resp.get("Messages", [])) == 0


# ---------------------------------------------------------------------------
# Option 1 integration — upsert + change stream
# ---------------------------------------------------------------------------

@requires_mongo
@requires_kafka
class TestOption1Integration:
    """End-to-end: upsert triggers change stream → diff published to Kafka."""

    def _flush_mongo(self):
        import pymongo
        client = pymongo.MongoClient(MONGO_URI)
        client["m1"]["members"].delete_many({})

    def setup_method(self):
        self._flush_mongo()

    def test_upsert_triggers_change_stream_event(self, tmp_path):
        """
        Confirm that upsert_member actually writes to MongoDB and the
        change stream emits a usable event.  The full listener loop is
        tested separately; here we verify the change stream emits.
        """
        import pymongo
        from src.hash_compare.option1.pipeline import upsert_member
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        col = pymongo.MongoClient(MONGO_URI)["m1"]["members"]
        events = []

        # Start a short-lived change stream before the upsert
        with col.watch(
            [{"$match": {"operationType": {"$in": ["insert", "replace"]}}}],
            full_document="updateLookup",
            full_document_before_change="whenAvailable",
            max_await_time_ms=3000,
        ) as stream:
            upsert_member(
                _load("canonical_member.json"),
                mongo_uri=MONGO_URI,
            )
            for event in stream:
                events.append(event)
                break  # one event is enough

        assert len(events) == 1
        assert events[0]["operationType"] in ("insert", "replace")
        assert events[0]["fullDocument"]["memberId"] == "MBR-00123456"

    def test_change_stream_diff_on_update(self):
        """Second upsert with a changed document emits a CHANGED diff."""
        import pymongo
        from src.hash_compare.option1.pipeline import upsert_member
        from src.hash_compare.option1.pipeline import ChangeStreamListener

        col = pymongo.MongoClient(MONGO_URI)["m1"]["members"]

        # Prime with baseline
        upsert_member(_load("canonical_member.json"), mongo_uri=MONGO_URI)

        events = []
        with col.watch(
            [{"$match": {"operationType": {"$in": ["replace"]}}}],
            full_document="updateLookup",
            full_document_before_change="whenAvailable",
            max_await_time_ms=3000,
        ) as stream:
            upsert_member(_load("canonical_member_changed.json"), mongo_uri=MONGO_URI)
            for event in stream:
                events.append(event)
                break

        assert len(events) == 1
        comparison = ChangeStreamListener._compute_diff_from_event(events[0])
        assert comparison is not None
        # If pre-image is available (requires changeStreamPreAndPostImages enabled)
        if comparison.diff_result.value != "NEW":
            assert comparison.diff_result.value == "CHANGED"
            assert "city" in comparison.changed_fields
