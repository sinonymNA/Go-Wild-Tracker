from __future__ import annotations

"""
GoWild Scraper — Playwright-based Frontier Airlines availability checker.

MOCK MODE (MOCK_MODE=true):
  Returns realistic fake data so the dashboard, database, alerts, and map can be
  tested before real selector tuning. All code paths are exercisable in mock mode.

REAL MODE (MOCK_MODE=false):
  Launches a Playwright Chromium browser, navigates to Frontier, and scrapes
  GoWild availability. Selector constants below are the primary tuning targets.
  Run with DEBUG_SCREENSHOTS=true and use the saved HTML in debug/ to identify
  the correct selectors.

IMPORTANT: This scraper never solves captchas, bypasses rate limits, or attempts
to defeat anti-bot systems. If a captcha or block is detected, the scraper logs
and returns an error status.
"""

import asyncio
import datetime
import logging
import random
import time
from pathlib import Path

from app.airport_data import get_airport_info
from app.config import get_settings
from app.models import ScanResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frontier URL template
# TODO: Confirm the exact search URL format from a real Frontier session
# ---------------------------------------------------------------------------
FRONTIER_SEARCH_URL = "https://www.flyfrontier.com/plan-trip/book-trip/flight-search/#/flight-search/one-way"

# ---------------------------------------------------------------------------
# CSS Selectors — ALL marked TODO. Tune using debug/ HTML captures.
# To debug: set DEBUG_SCREENSHOTS=true and HEADLESS=false, trigger a scan,
# then open the saved HTML in a browser to inspect the real DOM.
# ---------------------------------------------------------------------------
SELECTOR_FLIGHT_CARD = ".flight-card"          # TODO: container for a single flight result
SELECTOR_GOWILD_BADGE = "[data-testid='gowild-badge'], .go-wild-badge, .gowild"  # TODO
SELECTOR_PRICE = ".fare-price, .price-amount"  # TODO: tax/fee amount display
SELECTOR_DEPARTURE_TIME = ".departure-time, .depart-time"  # TODO
SELECTOR_ARRIVAL_TIME = ".arrival-time, .arrive-time"      # TODO
SELECTOR_NONSTOP = ".nonstop, [data-nonstop='true']"       # TODO
SELECTOR_CONNECTION = ".stop-count, .connection"           # TODO
SELECTOR_NO_FLIGHTS = ".no-results, .no-flights"           # TODO: shown when no results
SELECTOR_CAPTCHA = "[id*='captcha'], [class*='captcha'], iframe[src*='recaptcha']"  # detect blocks

# Connecting hub airports for mock data
_MOCK_HUBS = ["DEN", "PHX", "LAS", "MCO", "ORD", "DFW"]

# Common Frontier nonstop route pairs (used for mock is_nonstop determination)
_NONSTOP_PAIRS: set[frozenset[str]] = {
    frozenset({"ATL", "DEN"}), frozenset({"ATL", "MCO"}), frozenset({"ATL", "PHL"}),
    frozenset({"ATL", "DFW"}), frozenset({"ATL", "LAS"}), frozenset({"ATL", "ORD"}),
    frozenset({"ATL", "TPA"}), frozenset({"ATL", "FLL"}), frozenset({"ATL", "MDW"}),
    frozenset({"ATL", "CLT"}), frozenset({"ATL", "BWI"}), frozenset({"ATL", "CVG"}),
}


class GoWildScraper:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def search_route(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        if self.settings.MOCK_MODE:
            return await self._mock_search(origin, destination, date)
        return await self._real_search(origin, destination, date)

    # ------------------------------------------------------------------
    # Mock mode — deterministic fake data for UI/dashboard development
    # ------------------------------------------------------------------
    async def _mock_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        start = time.monotonic()
        # Simulate realistic network latency
        await asyncio.sleep(random.uniform(0.05, 0.3))

        # Deterministic seed so same route/date always gives same result
        rng = random.Random(f"{origin}-{destination}-{date}")

        available = rng.random() < 0.55

        prices = ["$5.60 taxes+fees", "$11.20 taxes+fees", "$0.00 taxes+fees", "$16.80 taxes+fees"]
        times_depart = ["06:15", "08:30", "10:45", "13:00", "15:20", "17:50", "19:10", "21:30"]
        duration_min = rng.randint(90, 300)
        dep_time = rng.choice(times_depart)
        dep_dt = datetime.datetime.strptime(dep_time, "%H:%M")
        arr_dt = dep_dt + datetime.timedelta(minutes=duration_min)
        arr_time = arr_dt.strftime("%H:%M")

        is_nonstop = frozenset({origin, destination}) in _NONSTOP_PAIRS or rng.random() < 0.35
        connection_info = None if is_nonstop else rng.choice(_MOCK_HUBS)

        origin_info = get_airport_info(origin)
        dest_info = get_airport_info(destination)

        elapsed_ms = int((time.monotonic() - start) * 1000) + rng.randint(500, 2500)

        return ScanResult(
            origin=origin,
            destination=destination,
            origin_lat=origin_info["lat"] if origin_info else None,
            origin_lon=origin_info["lon"] if origin_info else None,
            dest_lat=dest_info["lat"] if dest_info else None,
            dest_lon=dest_info["lon"] if dest_info else None,
            departure_date=date,
            departure_time=dep_time,
            arrival_time=arr_time,
            connection_info=connection_info,
            is_nonstop=is_nonstop,
            gowild_available=available,
            price_text=rng.choice(prices) if available else None,
            raw_status="MOCK_AVAILABLE" if available else "MOCK_UNAVAILABLE",
            scan_duration_ms=elapsed_ms,
            error=None,
        )

    # ------------------------------------------------------------------
    # Real mode — Playwright scraper skeleton with TODO selectors
    # ------------------------------------------------------------------
    async def _real_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

        start = time.monotonic()
        origin_info = get_airport_info(origin)
        dest_info = get_airport_info(destination)
        date_str = date.strftime("%Y-%m-%d")

        base_result = ScanResult(
            origin=origin,
            destination=destination,
            origin_lat=origin_info["lat"] if origin_info else None,
            origin_lon=origin_info["lon"] if origin_info else None,
            dest_lat=dest_info["lat"] if dest_info else None,
            dest_lon=dest_info["lon"] if dest_info else None,
            departure_date=date,
            gowild_available=False,
            raw_status="PENDING",
            is_nonstop=False,
        )

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=self.settings.HEADLESS,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                try:
                    # TODO: Build the correct Frontier search URL with origin/dest/date params.
                    # The current URL is a placeholder. Inspect real Frontier network requests
                    # to find the correct deep link format.
                    search_url = (
                        f"https://www.flyfrontier.com/plan-trip/book-trip/flight-search/"
                        f"#/flight-search/one-way?from={origin}&to={destination}&date={date_str}"
                    )
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

                    # Check for captcha before waiting for flights
                    if await page.query_selector(SELECTOR_CAPTCHA):
                        logger.warning("Captcha detected for %s→%s on %s — skipping", origin, destination, date_str)
                        base_result.raw_status = "BLOCKED_CAPTCHA"
                        return base_result

                    # Wait for flight results or no-results message
                    # TODO: Adjust the selectors and timeout based on real page behavior
                    try:
                        await page.wait_for_selector(
                            f"{SELECTOR_FLIGHT_CARD}, {SELECTOR_NO_FLIGHTS}",
                            timeout=30000,
                        )
                    except PlaywrightTimeout:
                        await self._save_debug(page, f"{origin}-{destination}-{date_str}-timeout")
                        base_result.raw_status = "TIMEOUT_WAITING_FOR_RESULTS"
                        return base_result

                    # Check for no-results state
                    if await page.query_selector(SELECTOR_NO_FLIGHTS):
                        base_result.raw_status = "NO_FLIGHTS_FOUND"
                        return base_result

                    # Find flight cards
                    cards = await page.query_selector_all(SELECTOR_FLIGHT_CARD)
                    if not cards:
                        await self._save_debug(page, f"{origin}-{destination}-{date_str}-no-cards")
                        base_result.raw_status = "NO_FLIGHT_CARDS_FOUND"
                        return base_result

                    # TODO: Iterate cards and find GoWild fare.
                    # The logic below is a skeleton — update once selectors are confirmed.
                    for card in cards:
                        gowild_el = await card.query_selector(SELECTOR_GOWILD_BADGE)
                        if not gowild_el:
                            continue

                        base_result.gowild_available = True

                        price_el = await card.query_selector(SELECTOR_PRICE)
                        if price_el:
                            base_result.price_text = (await price_el.inner_text()).strip()

                        dep_el = await card.query_selector(SELECTOR_DEPARTURE_TIME)
                        if dep_el:
                            base_result.departure_time = (await dep_el.inner_text()).strip()

                        arr_el = await card.query_selector(SELECTOR_ARRIVAL_TIME)
                        if arr_el:
                            base_result.arrival_time = (await arr_el.inner_text()).strip()

                        nonstop_el = await card.query_selector(SELECTOR_NONSTOP)
                        base_result.is_nonstop = nonstop_el is not None

                        if not base_result.is_nonstop:
                            conn_el = await card.query_selector(SELECTOR_CONNECTION)
                            if conn_el:
                                base_result.connection_info = (await conn_el.inner_text()).strip()

                        base_result.raw_status = "GOWILD_AVAILABLE"
                        break

                    if not base_result.gowild_available:
                        base_result.raw_status = "NO_GOWILD_FARE"

                finally:
                    await browser.close()

        except Exception as exc:
            logger.exception("Scraper error for %s→%s on %s", origin, destination, date_str)
            base_result.raw_status = f"ERROR: {type(exc).__name__}"
            base_result.error = str(exc)

        base_result.scan_duration_ms = int((time.monotonic() - start) * 1000)
        return base_result

    async def _save_debug(self, page, label: str) -> None:
        if not self.settings.DEBUG_SCREENSHOTS:
            return
        debug_dir = Path("debug")
        debug_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            await page.screenshot(path=str(debug_dir / f"{ts}_{label}.png"))
            html = await page.content()
            (debug_dir / f"{ts}_{label}.html").write_text(html, encoding="utf-8")
            logger.info("Debug capture saved: debug/%s_%s", ts, label)
        except Exception as exc:
            logger.warning("Failed to save debug capture: %s", exc)
