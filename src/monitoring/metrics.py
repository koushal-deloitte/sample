"""Metrics sink — emit to CloudWatch or log to stdout depending on config."""
import logging
from typing import Any

from src.config import settings

log = logging.getLogger(__name__)


def emit(metric_name: str, value: float, unit: str = "Count", dimensions: dict[str, str] | None = None) -> None:
    if settings.metrics_sink == "cloudwatch":
        _emit_cloudwatch(metric_name, value, unit, dimensions or {})
    else:
        log.info("metric", extra={"metric": metric_name, "value": value, "unit": unit, "dims": dimensions})


def _emit_cloudwatch(name: str, value: float, unit: str, dimensions: dict[str, str]) -> None:
    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=settings.aws_region)
        cw.put_metric_data(
            Namespace=settings.cloudwatch_namespace,
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
                }
            ],
        )
    except Exception as exc:
        log.warning("cloudwatch_emit_failed", extra={"metric": name, "error": str(exc)})
