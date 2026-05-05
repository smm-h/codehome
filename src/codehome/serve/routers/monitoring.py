"""Monitoring API routes: service health status and log history."""

from fastapi import APIRouter, Depends

from codehome.serve.dependencies import get_health_checker, get_log_aggregator
from codehome.serve.monitoring.health import HealthChecker
from codehome.serve.monitoring.logs import LogAggregator

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/health")
async def monitoring_health(
    checker: HealthChecker = Depends(get_health_checker),
) -> dict[str, object]:
    """Return the current health snapshot for all monitored services."""
    return checker.get_health()


@router.get("/logs/{service_key:path}")
async def get_service_logs(
    service_key: str,
    limit: int = 100,
    log_aggregator: LogAggregator = Depends(get_log_aggregator),
) -> dict[str, object]:
    """Return recent log entries for a service from the ring buffer."""
    lines = log_aggregator.get_logs(service_key, limit=min(limit, 1000))
    return {"service_key": service_key, "lines": lines}
