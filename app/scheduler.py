from __future__ import annotations

import asyncio
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import check_and_send_alerts
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import AppSettings, ScanResult
from app.routes_manager import get_enabled_routes
from app.scraper import GoWildScraper

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_last_run_at: datetime.datetime | None = None
_last_run_duration_s: float | None = None
_scan_running: bool = False


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_listener(_on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    return _scheduler


def _on_job_event(event) -> None:
    global _last_run_at
    _last_run_at = datetime.datetime.utcnow()


async def _get_setting(db: AsyncSession, key: str, default: str) -> str:
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else default


async def run_scan_job() -> None:
    global _scan_running, _last_run_at, _last_run_duration_s

    if _scan_running:
        logger.info("Scan already running — skipping this trigger")
        return

    _scan_running = True
    start = datetime.datetime.utcnow()
    settings = get_settings()
    logger.info("Scan job started")

    try:
        async with AsyncSessionLocal() as db:
            routes = await get_enabled_routes(db)
            if not routes:
                logger.info("No enabled routes — nothing to scan")
                return

            # Read per-scan settings from DB (allows live changes without restart)
            max_routes = int(
                await _get_setting(db, "MAX_ROUTES_PER_SCAN", str(settings.MAX_ROUTES_PER_SCAN))
            )
            delay = float(
                await _get_setting(db, "DELAY_BETWEEN_SEARCHES_SECONDS", str(settings.DELAY_BETWEEN_SEARCHES_SECONDS))
            )
            dates_ahead = int(
                await _get_setting(db, "SCAN_DATES_AHEAD", str(settings.SCAN_DATES_AHEAD))
            )

            routes_to_scan = routes[:max_routes]
            scan_dates = [
                datetime.date.today() + datetime.timedelta(days=i)
                for i in range(dates_ahead)
            ]

            scraper = GoWildScraper()
            new_results: list[ScanResult] = []

            for route in routes_to_scan:
                for scan_date in scan_dates:
                    result = await scraper.search_route(route.origin, route.destination, scan_date)
                    db.add(result)
                    new_results.append(result)

                    # Flush so result has an ID before alert check
                    await db.flush()
                    await db.commit()

                    if delay > 0:
                        await asyncio.sleep(delay)

                # Update last_scanned_at on the route
                route.last_scanned_at = datetime.datetime.utcnow()
                await db.commit()

            await check_and_send_alerts(db, new_results)

    except Exception:
        logger.exception("Scan job failed")
    finally:
        _scan_running = False
        _last_run_duration_s = (datetime.datetime.utcnow() - start).total_seconds()
        _last_run_at = datetime.datetime.utcnow()
        logger.info("Scan job finished in %.1fs", _last_run_duration_s)


def start_scheduler() -> None:
    settings = get_settings()
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.add_job(
            run_scan_job,
            trigger="interval",
            minutes=settings.SCAN_INTERVAL_MINUTES,
            id="main_scan",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info("Scheduler started — interval=%d min", settings.SCAN_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)


def pause_scheduler() -> None:
    get_scheduler().pause()


def resume_scheduler() -> None:
    get_scheduler().resume()


def get_scheduler_status() -> dict:
    from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED

    scheduler = get_scheduler()
    state = scheduler.state
    next_run = None
    try:
        job = scheduler.get_job("main_scan")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    except Exception:
        pass

    return {
        "running": state == STATE_RUNNING,
        "paused": state == STATE_PAUSED,
        "stopped": state == STATE_STOPPED,
        "scan_in_progress": _scan_running,
        "last_run_at": _last_run_at.isoformat() if _last_run_at else None,
        "last_run_duration_s": _last_run_duration_s,
        "next_run_at": next_run,
    }
