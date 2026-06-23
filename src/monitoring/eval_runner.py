"""
Nightly skill effectiveness job.

Run with:  python -m src.monitoring.eval_runner
"""
import asyncio
import logging

from sqlalchemy import text

from src.config import settings
from src.db.connection import AsyncSessionFactory
from src.monitoring.metrics import emit

log = logging.getLogger(__name__)


async def run_effectiveness_check() -> None:
    async with AsyncSessionFactory() as session:
        rows = await session.execute(
            text("""
                SELECT
                    s.id,
                    s.name,
                    COUNT(fr.id) AS total,
                    SUM(CASE WHEN fr.status = 'resolved' THEN 1 ELSE 0 END) AS resolved
                FROM skills s
                LEFT JOIN fallout_runs fr
                    ON fr.skill_id = s.id AND fr.created_at >= NOW() - INTERVAL '30 days'
                WHERE s.is_active = TRUE
                GROUP BY s.id, s.name
            """)
        )
        for row in rows:
            total = row.total or 0
            resolved = row.resolved or 0
            rate = resolved / total if total else None
            skill_name = row.name

            if rate is not None:
                emit("skill.resolution_rate", rate, unit="None", dimensions={"SkillName": skill_name})

            if rate is not None and rate < settings.skill_effectiveness_min_rate:
                log.warning(
                    "skill_below_threshold",
                    extra={
                        "skill_id": str(row.id),
                        "skill_name": skill_name,
                        "resolution_rate": rate,
                        "threshold": settings.skill_effectiveness_min_rate,
                    },
                )
                # STUB: send email/alert to business team


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("eval_runner_start")
    await run_effectiveness_check()
    log.info("eval_runner_complete")


if __name__ == "__main__":
    asyncio.run(main())
