from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthRedirect, create_session_cookie, require_auth, COOKIE_NAME
from app.config import get_settings
from app.database import AsyncSessionLocal, get_db, init_db
from app.models import AppSettings, Route, ScanResult
from app.routes_manager import (
    add_route,
    delete_route,
    get_all_routes,
    get_enabled_routes,
    load_routes_from_csv,
    seed_default_routes,
    toggle_route,
)
from app.scheduler import (
    get_scheduler_status,
    pause_scheduler,
    resume_scheduler,
    run_scan_job,
    start_scheduler,
    stop_scheduler,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up GoWild Radar…")
    await init_db()
    async with AsyncSessionLocal() as db:
        seeded = await seed_default_routes(db)
        if seeded:
            logger.info("Seeded %d default routes", seeded)
    start_scheduler()
    yield
    logger.info("Shutting down…")
    stop_scheduler()


app = FastAPI(title="GoWild Radar", docs_url=None, redoc_url=None, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Auth redirect exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def ctx(request: Request, **kwargs) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "mock_mode": settings.MOCK_MODE,
        "home_airport": settings.HOME_AIRPORT,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Unauthenticated routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", ctx(request))


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    from fastapi.responses import Response
    response = RedirectResponse(url="/", status_code=303)
    if not create_session_cookie(response, password):
        return templates.TemplateResponse(
            "login.html", ctx(request, error="Invalid password"), status_code=401
        )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Page routes (authenticated)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse("dashboard.html", ctx(request))


@app.get("/routes", response_class=HTMLResponse)
async def routes_page(request: Request, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    routes = await get_all_routes(db)
    return templates.TemplateResponse("routes.html", ctx(request, routes=routes))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    settings_map = await _load_settings_map(db)
    return templates.TemplateResponse("settings.html", ctx(request, settings_map=settings_map))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    result = await db.execute(
        select(ScanResult).order_by(ScanResult.checked_at.desc()).limit(200)
    )
    logs = result.scalars().all()
    return templates.TemplateResponse("logs.html", ctx(request, logs=logs))


# ---------------------------------------------------------------------------
# API — scan control
# ---------------------------------------------------------------------------

@app.post("/api/scan/run")
async def api_scan_run(background_tasks: BackgroundTasks, _=Depends(require_auth)):
    background_tasks.add_task(run_scan_job)
    return {"status": "scan_triggered"}


@app.post("/api/scan/pause")
async def api_scan_pause(_=Depends(require_auth)):
    pause_scheduler()
    return {"status": "paused"}


@app.post("/api/scan/resume")
async def api_scan_resume(_=Depends(require_auth)):
    resume_scheduler()
    return {"status": "resumed"}


# ---------------------------------------------------------------------------
# API — stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def api_stats(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    total_result = await db.execute(select(func.count()).select_from(ScanResult))
    total = total_result.scalar() or 0

    avail_result = await db.execute(
        select(func.count()).select_from(ScanResult).where(ScanResult.gowild_available == True)  # noqa: E712
    )
    available = avail_result.scalar() or 0

    error_result = await db.execute(
        select(func.count()).select_from(ScanResult).where(ScanResult.error != None)  # noqa: E711
    )
    errors = error_result.scalar() or 0

    last_result = await db.execute(
        select(ScanResult.checked_at).order_by(ScanResult.checked_at.desc()).limit(1)
    )
    last_checked = last_result.scalar_one_or_none()

    route_count_result = await db.execute(select(func.count()).select_from(Route))
    route_count = route_count_result.scalar() or 0

    enabled_result = await db.execute(
        select(func.count()).select_from(Route).where(Route.enabled == True)  # noqa: E712
    )
    enabled_count = enabled_result.scalar() or 0

    scheduler_status = get_scheduler_status()

    return {
        "total_results": total,
        "available_count": available,
        "error_count": errors,
        "last_checked_at": last_checked.isoformat() if last_checked else None,
        "route_count": route_count,
        "enabled_routes": enabled_count,
        "scheduler": scheduler_status,
    }


# ---------------------------------------------------------------------------
# API — results
# ---------------------------------------------------------------------------

@app.get("/api/results")
async def api_results(
    request: Request,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    date: Optional[str] = None,
    available_only: bool = False,
    nonstop_only: bool = False,
    limit: int = 500,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    stmt = select(ScanResult).order_by(ScanResult.checked_at.desc())
    if origin:
        stmt = stmt.where(ScanResult.origin == origin.upper())
    if dest:
        stmt = stmt.where(ScanResult.destination == dest.upper())
    if date:
        stmt = stmt.where(ScanResult.departure_date == date)
    if available_only:
        stmt = stmt.where(ScanResult.gowild_available == True)  # noqa: E712
    if nonstop_only:
        stmt = stmt.where(ScanResult.is_nonstop == True)  # noqa: E712
    stmt = stmt.offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [r.to_dict() for r in rows]


# ---------------------------------------------------------------------------
# API — routes CRUD
# ---------------------------------------------------------------------------

@app.get("/api/routes")
async def api_routes(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    routes = await get_all_routes(db)
    return [
        {
            "id": r.id,
            "origin": r.origin,
            "destination": r.destination,
            "enabled": r.enabled,
            "last_scanned_at": r.last_scanned_at.isoformat() if r.last_scanned_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in routes
    ]


@app.post("/api/routes")
async def api_add_route(
    origin: str = Form(...),
    destination: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    route = await add_route(db, origin, destination)
    if not route:
        raise HTTPException(400, "Could not add route — invalid or duplicate")
    return RedirectResponse(url="/routes", status_code=303)


@app.delete("/api/routes/{route_id}")
async def api_delete_route(route_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    ok = await delete_route(db, route_id)
    if not ok:
        raise HTTPException(404, "Route not found")
    return {"status": "deleted"}


@app.put("/api/routes/{route_id}/toggle")
async def api_toggle_route(
    route_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    route = await toggle_route(db, route_id, enabled)
    if not route:
        raise HTTPException(404, "Route not found")
    return {"id": route.id, "enabled": route.enabled}


@app.post("/api/routes/bulk")
async def api_bulk_routes(
    csv_text: Optional[str] = Form(None),
    csv_file: Optional[UploadFile] = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    if csv_file and csv_file.filename:
        raw = await csv_file.read()
        text = raw.decode("utf-8-sig")
    elif csv_text:
        text = csv_text
    else:
        raise HTTPException(400, "Provide csv_text or csv_file")

    result = await load_routes_from_csv(db, text)
    return result


# ---------------------------------------------------------------------------
# API — settings
# ---------------------------------------------------------------------------

EDITABLE_SETTINGS = [
    "HOME_AIRPORT",
    "SCAN_INTERVAL_MINUTES",
    "MAX_ROUTES_PER_SCAN",
    "DELAY_BETWEEN_SEARCHES_SECONDS",
    "SCAN_DATES_AHEAD",
    "DISCORD_WEBHOOK_URL",
    "DEBUG_SCREENSHOTS",
    "SHOW_UNAVAILABLE_ON_MAP",
]


async def _load_settings_map(db: AsyncSession) -> dict:
    settings = get_settings()
    defaults = {
        "HOME_AIRPORT": settings.HOME_AIRPORT,
        "SCAN_INTERVAL_MINUTES": str(settings.SCAN_INTERVAL_MINUTES),
        "MAX_ROUTES_PER_SCAN": str(settings.MAX_ROUTES_PER_SCAN),
        "DELAY_BETWEEN_SEARCHES_SECONDS": str(settings.DELAY_BETWEEN_SEARCHES_SECONDS),
        "SCAN_DATES_AHEAD": str(settings.SCAN_DATES_AHEAD),
        "DISCORD_WEBHOOK_URL": settings.DISCORD_WEBHOOK_URL or "",
        "DEBUG_SCREENSHOTS": "true" if settings.DEBUG_SCREENSHOTS else "false",
        "SHOW_UNAVAILABLE_ON_MAP": "true",
    }
    result = await db.execute(select(AppSettings))
    for row in result.scalars().all():
        defaults[row.key] = row.value
    return defaults


@app.get("/api/settings")
async def api_get_settings(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    return await _load_settings_map(db)


@app.post("/api/settings")
async def api_save_settings(request: Request, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    form = await request.form()
    for key in EDITABLE_SETTINGS:
        value = form.get(key, "")
        # Handle checkbox booleans
        if key in ("DEBUG_SCREENSHOTS", "SHOW_UNAVAILABLE_ON_MAP"):
            value = "true" if form.get(key) else "false"
        result = await db.execute(select(AppSettings).where(AppSettings.key == key))
        row = result.scalar_one_or_none()
        if row:
            row.value = str(value)
        else:
            db.add(AppSettings(key=key, value=str(value)))
    await db.commit()
    return RedirectResponse(url="/settings", status_code=303)
