from __future__ import annotations

import csv
import io
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.airport_data import AIRPORT_CODES
from app.models import Route


async def get_all_routes(db: AsyncSession) -> list[Route]:
    result = await db.execute(select(Route).order_by(Route.origin, Route.destination))
    return list(result.scalars().all())


async def get_enabled_routes(db: AsyncSession) -> list[Route]:
    result = await db.execute(
        select(Route)
        .where(Route.enabled == True)  # noqa: E712
        .order_by(Route.last_scanned_at.asc().nulls_first(), Route.id.asc())
    )
    return list(result.scalars().all())


async def get_route_count(db: AsyncSession) -> int:
    result = await db.execute(select(Route))
    return len(result.scalars().all())


async def add_route(db: AsyncSession, origin: str, destination: str) -> Route | None:
    origin = origin.upper().strip()
    destination = destination.upper().strip()
    if not origin or not destination or origin == destination:
        return None

    # Check if exists (including disabled)
    result = await db.execute(
        select(Route).where(Route.origin == origin, Route.destination == destination)
    )
    existing = result.scalar_one_or_none()
    if existing:
        if not existing.enabled:
            existing.enabled = True
            await db.commit()
            await db.refresh(existing)
        return existing

    route = Route(origin=origin, destination=destination, enabled=True)
    db.add(route)
    try:
        await db.commit()
        await db.refresh(route)
        return route
    except IntegrityError:
        await db.rollback()
        return None


async def toggle_route(db: AsyncSession, route_id: int, enabled: bool) -> Route | None:
    result = await db.execute(select(Route).where(Route.id == route_id))
    route = result.scalar_one_or_none()
    if not route:
        return None
    route.enabled = enabled
    await db.commit()
    await db.refresh(route)
    return route


async def delete_route(db: AsyncSession, route_id: int) -> bool:
    result = await db.execute(select(Route).where(Route.id == route_id))
    route = result.scalar_one_or_none()
    if not route:
        return False
    await db.delete(route)
    await db.commit()
    return True


async def load_routes_from_csv(
    db: AsyncSession, csv_text: str
) -> dict:
    added = 0
    skipped = 0
    errors: list[str] = []

    reader = csv.reader(io.StringIO(csv_text.strip().lstrip("﻿")))
    for i, row in enumerate(reader):
        if not row:
            continue
        row = [c.strip().upper() for c in row]
        if row[0] in ("ORIGIN", "FROM", "SRC"):
            continue  # header row
        if len(row) < 2:
            errors.append(f"Row {i+1}: not enough columns — {row}")
            continue
        origin, dest = row[0], row[1]
        if len(origin) != 3 or len(dest) != 3:
            errors.append(f"Row {i+1}: invalid IATA codes {origin}/{dest}")
            skipped += 1
            continue
        route = await add_route(db, origin, dest)
        if route:
            added += 1
        else:
            skipped += 1

    return {"added": added, "skipped": skipped, "errors": errors}


async def seed_default_routes(db: AsyncSession) -> int:
    count = await get_route_count(db)
    if count > 0:
        return 0

    csv_path = Path(__file__).parent.parent / "data" / "seed_routes.csv"
    if not csv_path.exists():
        return 0

    csv_text = csv_path.read_text(encoding="utf-8-sig")
    result = await load_routes_from_csv(db, csv_text)
    return result["added"]
