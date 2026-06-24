"""
Option 2: File → MongoDB Subscriber Snapshot Compare → Kafka (or SQS)

Flow:
  1. Load canonical JSON from file
  2. Fetch the current baseline snapshot from MongoDB (keyed on memberId + groupId)
  3. Compute SHA-256 hashes in-application (hasher.py)
  4. Classify as NEW / CHANGED / NOOP / REMOVE
  5. If not NOOP  → publish HashComparison event to Kafka topic (or SQS queue)
               → upsert the incoming document as the new baseline in MongoDB
  6. If NOOP     → skip the write entirely (no DB write, no event)

Trade-offs vs Option 1:
  + Explicit, predictable: the diff is computed before the write, giving the
    caller full control over whether to write at all (NOOP = skip the upsert).
  + No Kafka Connect / Debezium infrastructure required; the application is
    the only change-detection layer.
  + Lower latency: one round-trip (read baseline → compare → publish + write)
    vs two (write → CDC → consumer).
  + No pre-image storage requirement on MongoDB.
  - Every writer must go through this pipeline to ensure diff events are
    produced; a direct MongoDB write bypasses the comparison.
  - Hash must be recomputed on every ingest, even for large members (typically
    < 1 ms for a ~190-field document).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import boto3  # module-level so tests can patch src.hash_compare.option2.pipeline.boto3

from ..hasher import diff_members
from ..models import DiffResult, HashComparison

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def _mongo_collection(mongo_uri: str, db: str = "m1", collection: str = "members"):
    import pymongo  # type: ignore

    client = pymongo.MongoClient(mongo_uri)
    return client[db][collection]


def _fetch_baseline(
    member_id: str,
    group_id: str,
    mongo_uri: str,
    db: str = "m1",
    collection: str = "members",
) -> dict[str, Any] | None:
    col = _mongo_collection(mongo_uri, db, collection)
    doc = col.find_one(
        {"memberId": member_id, "groupId": group_id},
        projection={"_id": 0},
    )
    return doc


def _upsert_baseline(
    member: dict[str, Any],
    mongo_uri: str,
    db: str = "m1",
    collection: str = "members",
) -> None:
    col = _mongo_collection(mongo_uri, db, collection)
    col.replace_one(
        {"memberId": member["memberId"], "groupId": member["groupId"]},
        member,
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Kafka publisher
# ---------------------------------------------------------------------------

def _publish_kafka(
    comparison: HashComparison,
    bootstrap: str,
    topic: str,
) -> None:
    from kafka import KafkaProducer  # type: ignore

    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode(),
        key_serializer=lambda k: k.encode() if k else None,
        acks="all",
        retries=3,
    )
    key = f"{comparison.member_id}:{comparison.group_id}"
    producer.send(topic, key=key, value=comparison.to_event())
    producer.flush()
    producer.close()
    logger.info(
        "kafka published memberId=%s result=%s topic=%s",
        comparison.member_id,
        comparison.diff_result.value,
        topic,
    )


# ---------------------------------------------------------------------------
# SQS publisher (alternative transport)
# ---------------------------------------------------------------------------

def _publish_sqs(
    comparison: HashComparison,
    queue_url: str,
    aws_region: str = "us-east-1",
    endpoint_url: str | None = None,
) -> None:
    kwargs: dict[str, Any] = {"region_name": aws_region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    sqs = boto3.client("sqs", **kwargs)
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=comparison.to_json(),
        MessageAttributes={
            "diffResult": {
                "StringValue": comparison.diff_result.value,
                "DataType": "String",
            },
            "memberId": {
                "StringValue": comparison.member_id,
                "DataType": "String",
            },
        },
    )
    logger.info(
        "sqs published memberId=%s result=%s queue=%s",
        comparison.member_id,
        comparison.diff_result.value,
        queue_url,
    )


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def process_file(
    file_path: str | Path,
    mongo_uri: str,
    kafka_bootstrap: str | None = None,
    kafka_topic: str = "m1.member.diff",
    sqs_queue_url: str | None = None,
    sqs_region: str = "us-east-1",
    sqs_endpoint_url: str | None = None,
    db: str = "m1",
    collection: str = "members",
) -> HashComparison:
    """
    Compare an incoming canonical member file against its MongoDB baseline and
    publish a diff event.  Returns the HashComparison result.

    At least one of kafka_bootstrap or sqs_queue_url must be supplied when the
    result is not NOOP.
    """
    if kafka_bootstrap is None and sqs_queue_url is None:
        raise ValueError("Supply kafka_bootstrap or sqs_queue_url (or both).")

    path = Path(file_path)
    incoming = json.loads(path.read_text())

    # Strip internal JSON comment added by fixtures
    incoming.pop("_comment", None)

    member_id: str = incoming["memberId"]
    group_id: str = incoming["groupId"]

    baseline = _fetch_baseline(member_id, group_id, mongo_uri, db, collection)

    comparison = diff_members(incoming=incoming, baseline=baseline)

    logger.info(
        "option2: memberId=%s result=%s changed=%s",
        member_id,
        comparison.diff_result.value,
        comparison.changed_fields,
    )

    if comparison.diff_result is DiffResult.NOOP:
        logger.info("option2: NOOP — skipping write and publish for memberId=%s", member_id)
        return comparison

    # Publish before write so a publish failure leaves baseline unchanged
    if kafka_bootstrap:
        _publish_kafka(comparison, kafka_bootstrap, kafka_topic)

    if sqs_queue_url:
        _publish_sqs(comparison, sqs_queue_url, sqs_region, sqs_endpoint_url)

    # Persist new baseline
    _upsert_baseline(incoming, mongo_uri, db, collection)

    return comparison
