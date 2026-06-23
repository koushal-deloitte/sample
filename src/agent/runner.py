"""SQS polling loop — the main entry point for the fallout processing agent."""
import asyncio
import json
import logging
import signal

import boto3
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import orchestrator
from src.config import settings
from src.db.connection import AsyncSessionFactory
from src.ehap.client import EHAPClient
from src.models.fallout import FalloutRecord

log = logging.getLogger(__name__)


class FalloutRunner:
    def __init__(self) -> None:
        self._running = True
        self._ehap = EHAPClient()
        self._sqs = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    def stop(self) -> None:
        log.info("runner_shutdown_requested")
        self._running = False

    async def run(self) -> None:
        log.info("fallout_runner_started", extra={"queue": settings.sqs_queue_url})
        while self._running:
            try:
                await self._poll()
            except Exception as exc:
                log.error("poll_error", extra={"error": str(exc)})
            await asyncio.sleep(settings.sqs_poll_interval_seconds)
        await self._ehap.close()

    async def _poll(self) -> None:
        response = self._sqs.receive_message(
            QueueUrl=settings.sqs_queue_url,
            MaxNumberOfMessages=settings.sqs_max_messages,
            WaitTimeSeconds=5,
        )
        messages = response.get("Messages", [])
        if not messages:
            return

        log.info("sqs_messages_received", extra={"count": len(messages)})
        tasks = [self._handle_message(msg) for msg in messages]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, sqs_message: dict) -> None:
        receipt_handle = sqs_message["ReceiptHandle"]
        try:
            body = json.loads(sqs_message["Body"])
            record = FalloutRecord(
                transaction_id=body["transaction_id"],
                error_code=body.get("error_code", "UNKNOWN"),
                error_message=body.get("error_message", ""),
                raw_sqs_body=body,
            )
        except (KeyError, ValueError) as exc:
            log.error("invalid_sqs_message", extra={"error": str(exc), "body": sqs_message.get("Body", "")[:200]})
            return

        async with AsyncSessionFactory() as session:
            result = await orchestrator.process(record, self._ehap, session)

        if result.status in {"resolved", "escalated"}:
            self._sqs.delete_message(QueueUrl=settings.sqs_queue_url, ReceiptHandle=receipt_handle)
            log.info("sqs_message_deleted", extra={"transaction_id": record.transaction_id, "status": result.status})
        else:
            log.warning("sqs_message_not_deleted", extra={"transaction_id": record.transaction_id, "status": result.status})


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s %(extra)s")
    runner = FalloutRunner()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, runner.stop)

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
