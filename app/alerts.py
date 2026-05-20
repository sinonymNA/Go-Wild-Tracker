from __future__ import annotations

import logging
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ScanResult

logger = logging.getLogger(__name__)


def _build_frontier_url(origin: str, destination: str, date: str) -> str:
    # TODO: confirm the exact deep-link format once real Frontier pages are inspected
    return (
        f"https://www.flyfrontier.com/plan-trip/low-fare-calendar/"
        f"?from={origin}&to={destination}&date={date}"
    )


async def check_and_send_alerts(db: AsyncSession, new_results: list[ScanResult]) -> None:
    settings = get_settings()
    if not settings.DISCORD_WEBHOOK_URL:
        return

    for result in new_results:
        if not result.gowild_available:
            continue

        # Find the previous result for this route + date
        stmt = (
            select(ScanResult)
            .where(
                ScanResult.origin == result.origin,
                ScanResult.destination == result.destination,
                ScanResult.departure_date == result.departure_date,
                ScanResult.id != result.id,
            )
            .order_by(ScanResult.checked_at.desc())
            .limit(1)
        )
        prev_result = await db.execute(stmt)
        previous = prev_result.scalar_one_or_none()

        if previous and previous.gowild_available:
            continue  # Already was available — no state change

        await _send_discord_alert(result)


async def _send_discord_alert(result: ScanResult) -> None:
    settings = get_settings()
    if not settings.DISCORD_WEBHOOK_URL:
        return

    date_str = str(result.departure_date) if result.departure_date else "Unknown"
    frontier_url = _build_frontier_url(result.origin, result.destination, date_str)
    connection_text = "Nonstop" if result.is_nonstop else f"1 stop ({result.connection_info or 'connection'})"

    payload = {
        "embeds": [
            {
                "title": f"✈️ GoWild Available: {result.origin} → {result.destination}",
                "color": 0x22C55E,
                "fields": [
                    {"name": "Route", "value": f"{result.origin} → {result.destination}", "inline": True},
                    {"name": "Date", "value": date_str, "inline": True},
                    {"name": "Departs", "value": result.departure_time or "?", "inline": True},
                    {"name": "Arrives", "value": result.arrival_time or "?", "inline": True},
                    {"name": "Price/Fees", "value": result.price_text or "GoWild (pass required)", "inline": True},
                    {"name": "Type", "value": connection_text, "inline": True},
                    {"name": "Book Now", "value": frontier_url, "inline": False},
                ],
                "footer": {"text": "GoWild Radar"},
                "timestamp": datetime.utcnow().isoformat(),
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.DISCORD_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Discord alert failed: %s", exc)
