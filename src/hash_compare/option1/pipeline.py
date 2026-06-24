"""
Option 1: File → MongoDB Upsert → Change Stream (CDC) → Kafka

Flow:
  1. Load canonical JSON from file
  2. Upsert into MongoDB members collection (keyed on memberId + groupId)
  3. MongoDB change stream emits an insert/update/delete event
  4. A stream listener picks up the event, computes the hash diff
     (comparing fullDocument vs fullDocumentBeforeChange), and publishes
     a DiffEvent to a Kafka topic.

MongoDB requirements:
  - Replica Set (change streams require oplog)
  - fullDocumentBeforeChange: "whenAvailable" option on the change stream
    (requires MongoDB 6.0+ or a pre-image policy on the collection)

Trade-offs vs Option 2:
  + Source of truth for "what actually landed in the DB" — the diff fires
    only after the write commits, not before.
  + Decoupled: any writer (not just this pipeline) that upserts a member
    automatically produces a diff event — useful for multi-source writes.
  - Requires Kafka Connect / Debezium OR a long-lived change-stream listener
    process. If the listener crashes and misses events, recovery needs
    resume tokens.
  - Pre-image (before-change document) must be enabled on the collection;
    not the default and adds storage overhead.
  - Latency is two hops: write → change stream → Kafka consumer → diff.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..hasher import diff_members
from ..models import DiffResult, HashComparison

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def _mongo_collection(mongo_uri: str, db: str = "m1", collection: str = "members"):
    """Return a pymongo Collection. Import deferred so tests can mock."""
    import pymongo  # type: ignore

    client = pymongo.MongoClient(mongo_uri)
    return client[db][collection]


def upsert_member(
    member: dict[str, Any],
    mongo_uri: str,
    db: str = "m1",
    collection: str = "members",
) -> None:
    """
    Upsert a canonical member document.  The document key is (memberId, groupId).
    Using replaceOne with upsert=True so the change stream emits the full
    replacement document which carries the new and (via pre-image) old state.
    """
    col = _mongo_collection(mongo_uri, db, collection)
    col.replace_one(
        filter={"memberId": member["memberId"], "groupId": member["groupId"]},
        replacement=member,
        upsert=True,
    )
    logger.info(
        "upserted memberId=%s groupId=%s", member["memberId"], member["groupId"]
    )


# ---------------------------------------------------------------------------
# Change stream listener
# ---------------------------------------------------------------------------

class ChangeStreamListener:
    """
    Long-lived listener that tails the MongoDB change stream and publishes
    HashComparison events to Kafka.

    resume_token is persisted so the listener can recover missed events after
    a restart without replaying the entire oplog from the beginning.
    """

    def __init__(
        self,
        mongo_uri: str,
        kafka_bootstrap: str,
        topic: str = "m1.member.diff",
        db: str = "m1",
        collection: str = "members",
        resume_token_path: str | None = None,
    ) -> None:
        self._mongo_uri = mongo_uri
        self._kafka_bootstrap = kafka_bootstrap
        self._topic = topic
        self._db = db
        self._collection = collection
        self._resume_token_path = Path(resume_token_path) if resume_token_path else None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Kafka producer (lazy import so pymongo-only tests skip kafka-python)
    # ------------------------------------------------------------------

    def _make_producer(self):
        from kafka import KafkaProducer  # type: ignore

        return KafkaProducer(
            bootstrap_servers=self._kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v).encode(),
            key_serializer=lambda k: k.encode() if k else None,
            acks="all",
            retries=3,
        )

    # ------------------------------------------------------------------
    # Resume token persistence
    # ------------------------------------------------------------------

    def _load_resume_token(self) -> dict | None:
        if self._resume_token_path and self._resume_token_path.exists():
            try:
                return json.loads(self._resume_token_path.read_text())
            except Exception:
                return None
        return None

    def _save_resume_token(self, token: dict) -> None:
        if self._resume_token_path:
            self._resume_token_path.write_text(json.dumps(token))

    # ------------------------------------------------------------------
    # Diff computation from a CDC event
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_diff_from_event(event: dict) -> HashComparison | None:
        op = event.get("operationType")
        full_doc = event.get("fullDocument")
        pre_image = event.get("fullDocumentBeforeChange")

        if op == "delete":
            # fullDocument is absent on delete; reconstruct from preImage
            if pre_image:
                return HashComparison(
                    member_id=pre_image.get("memberId", "unknown"),
                    group_id=pre_image.get("groupId", "unknown"),
                    diff_result=DiffResult.REMOVE,
                    incoming_hash="",
                    baseline_hash="",
                    changed_fields=[],
                    comparison_field_count=0,
                    excluded_volatile_fields=[],
                )
            return None

        if not full_doc:
            return None

        return diff_members(
            incoming=full_doc,
            baseline=pre_image,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        col = _mongo_collection(self._mongo_uri, self._db, self._collection)
        producer = self._make_producer()
        resume_token = self._load_resume_token()

        pipeline = [{"$match": {"operationType": {"$in": ["insert", "replace", "delete"]}}}]
        stream_opts: dict[str, Any] = {
            "full_document": "updateLookup",
            "full_document_before_change": "whenAvailable",
        }
        if resume_token:
            stream_opts["resume_after"] = resume_token

        logger.info("Starting change stream listener on %s.%s", self._db, self._collection)

        with col.watch(pipeline, **stream_opts) as stream:
            for event in stream:
                if self._stop_event.is_set():
                    break

                comparison = self._compute_diff_from_event(event)
                if comparison is None:
                    logger.debug("Skipping unclassifiable event op=%s", event.get("operationType"))
                else:
                    key = f"{comparison.member_id}:{comparison.group_id}"
                    producer.send(self._topic, key=key, value=comparison.to_event())
                    logger.info(
                        "published diff memberId=%s result=%s",
                        comparison.member_id,
                        comparison.diff_result.value,
                    )

                self._save_resume_token(event["_id"])
                producer.flush()

        producer.close()

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def process_file(
    file_path: str | Path,
    mongo_uri: str,
    kafka_bootstrap: str,
    topic: str = "m1.member.diff",
) -> None:
    """
    Load a canonical member JSON file and upsert into MongoDB.
    The change stream listener (run separately) will pick up the change
    and publish the diff event to Kafka.
    """
    path = Path(file_path)
    member = json.loads(path.read_text())
    upsert_member(member, mongo_uri)
    logger.info(
        "option1: file=%s memberId=%s ingested; CDC listener will produce diff event",
        path.name,
        member.get("memberId"),
    )
