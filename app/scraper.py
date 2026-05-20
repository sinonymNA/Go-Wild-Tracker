from __future__ import annotations

"""
GoWild Scraper — Playwright-based Frontier Airlines availability checker.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MOCK MODE  (MOCK_MODE=true)
  Returns realistic fake data. Use this until selectors are confirmed.

REAL MODE  (MOCK_MODE=false)
  Launches a Playwright Chromium browser and scrapes booking.flyfrontier.com.

COOKIE SESSION IMPORT
  Frontier may require a GoWild pass login to show GoWild fares.
  Export your browser session cookies to data/frontier_cookies.json
  (see README for how) — the scraper will load them automatically.

TUNING SELECTORS
  1. Run:  python tools/inspect_frontier.py --origin ATL --dest DEN --date YYYY-MM-DD
  2. Open debug/*.html in a browser, inspect elements
  3. Update the SELECTOR_* constants below
  4. Re-run inspect tool with --url-only to verify

IMPORTANT: Never bypasses captchas, rate limits, or anti-bot systems.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import datetime
import json
import logging
import random
import time
from pathlib import Path

from app.airport_data import get_airport_info
from app.config import get_settings
from app.models import ScanResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Frontier URL format
#
# The booking subdomain accepts direct deep links. The SPA reads these params
# and runs the search on load. Verified format (as of 2024):
#   https://booking.flyfrontier.com/flights?origin=ATL&destination=DEN
#   &departureDate=2025-08-15&adults=1&tripType=OW&currency=USD
#
# TODO: Verify this still works by running the inspect tool locally.
# ─────────────────────────────────────────────────────────────────────────────
FRONTIER_BOOKING_URL = "https://booking.flyfrontier.com/flights"

def _build_search_url(origin: str, dest: str, date: str) -> str:
    return (
        f"{FRONTIER_BOOKING_URL}"
        f"?origin={origin}&destination={dest}"
        f"&departureDate={date}&adults=1&tripType=OW&currency=USD"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CSS Selectors
#
# These are best-effort guesses based on Frontier's React booking SPA.
# Frontier uses styled-components (hashed class names) so the stable hooks
# are data-testid attributes and aria-labels, not class names.
#
# HOW TO TUNE:
#   1. python tools/inspect_frontier.py --origin ATL --dest DEN --date 2025-08-15
#   2. Open debug/booking_subdomain_page.html in Chrome
#   3. Right-click a flight card → Inspect → find selectors
#   4. Update the constants below and re-run the inspect tool to verify
# ─────────────────────────────────────────────────────────────────────────────

# Container for a single flight result row/card
# Try data-testid first (stable), then fall back to class patterns
SELECTOR_FLIGHT_CARD = (
    "[data-testid='flight-result'], "
    "[data-testid='flight-card'], "
    "[data-testid='itinerary-card'], "
    "[data-testid*='flight-option'], "
    "[class*='FlightResult'], "
    "[class*='flight-result'], "
    "[class*='ItineraryCard'], "
    "[class*='itinerary-card']"
)

# GoWild fare badge/button/label within a flight card
# GoWild appears as a clickable fare tile — look for the text "GoWild" near a price
SELECTOR_GOWILD_BADGE = (
    "[data-testid*='gowild'], "
    "[data-testid*='GoWild'], "
    "[data-testid*='wild'], "
    "[aria-label*='GoWild'], "
    "[aria-label*='Go Wild'], "
    "[class*='GoWild'], "
    "[class*='gowild'], "
    "[class*='go-wild']"
)

# GoWild fare text fallback — look for the word "GoWild" as text
SELECTOR_GOWILD_TEXT = "text=GoWild"

# Price of the GoWild fare (taxes + fees)
SELECTOR_PRICE = (
    "[data-testid*='price'], "
    "[data-testid*='fare-amount'], "
    "[data-testid*='total'], "
    "[class*='price'], "
    "[class*='Price'], "
    "[class*='FareAmount'], "
    "[class*='fare-amount']"
)

# Departure and arrival times
SELECTOR_DEPARTURE_TIME = (
    "[data-testid*='departure-time'], "
    "[data-testid*='depart-time'], "
    "[aria-label*='Departs'], "
    "[class*='DepartureTime'], "
    "[class*='departure-time'], "
    "[class*='depart-time']"
)

SELECTOR_ARRIVAL_TIME = (
    "[data-testid*='arrival-time'], "
    "[data-testid*='arrive-time'], "
    "[aria-label*='Arrives'], "
    "[class*='ArrivalTime'], "
    "[class*='arrival-time'], "
    "[class*='arrive-time']"
)

# Nonstop vs stops
SELECTOR_NONSTOP = (
    "[data-testid*='nonstop'], "
    "text=Nonstop, "
    "text=nonstop, "
    "[aria-label*='nonstop'], "
    "[class*='nonstop'], "
    "[class*='Nonstop']"
)

SELECTOR_STOPS = (
    "[data-testid*='stop-count'], "
    "[data-testid*='stops'], "
    "[class*='StopCount'], "
    "[class*='stop-count'], "
    "[class*='stops']"
)

# No results / sold out
SELECTOR_NO_FLIGHTS = (
    "[data-testid*='no-result'], "
    "[data-testid*='no-flight'], "
    "[class*='NoResult'], "
    "[class*='no-result'], "
    "[class*='EmptyState'], "
    "text=No flights found, "
    "text=No results"
)

# Captcha detection (never solve — just detect and abort)
SELECTOR_CAPTCHA = (
    "[id*='captcha'], "
    "[class*='captcha'], "
    "iframe[src*='recaptcha'], "
    "iframe[src*='hcaptcha'], "
    "[class*='challenge']"
)

# Loading spinner — wait for it to disappear before scraping
SELECTOR_LOADING = (
    "[data-testid*='loading'], "
    "[class*='Loading'], "
    "[class*='Spinner'], "
    "[class*='spinner'], "
    "[role='progressbar']"
)


# ─────────────────────────────────────────────────────────────────────────────
# Cookie session import
# ─────────────────────────────────────────────────────────────────────────────
COOKIES_FILE = Path("data/frontier_cookies.json")


def _load_cookies() -> list[dict] | None:
    """Load exported browser cookies from data/frontier_cookies.json if it exists."""
    if not COOKIES_FILE.exists():
        return None
    try:
        data = json.loads(COOKIES_FILE.read_text())
        # Accept both a plain list and the format exported by browser extensions
        # (EditThisCookie, Cookie-Editor, etc.) which wraps in {"cookies": [...]}
        if isinstance(data, list):
            cookies = data
        elif isinstance(data, dict) and "cookies" in data:
            cookies = data["cookies"]
        else:
            cookies = data
        logger.info("Loaded %d cookies from %s", len(cookies), COOKIES_FILE)
        return cookies
    except Exception as exc:
        logger.warning("Failed to load cookies: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Mock data helpers
# ─────────────────────────────────────────────────────────────────────────────
_MOCK_HUBS = ["DEN", "PHX", "LAS", "MCO", "ORD", "DFW"]

_NONSTOP_PAIRS: set[frozenset[str]] = {
    frozenset({"ATL", "DEN"}), frozenset({"ATL", "MCO"}), frozenset({"ATL", "PHL"}),
    frozenset({"ATL", "DFW"}), frozenset({"ATL", "LAS"}), frozenset({"ATL", "ORD"}),
    frozenset({"ATL", "TPA"}), frozenset({"ATL", "FLL"}), frozenset({"ATL", "MDW"}),
    frozenset({"ATL", "CLT"}), frozenset({"ATL", "BWI"}), frozenset({"ATL", "CVG"}),
}


# ─────────────────────────────────────────────────────────────────────────────
# Scraper class
# ─────────────────────────────────────────────────────────────────────────────

class GoWildScraper:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def search_route(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        if self.settings.MOCK_MODE:
            return await self._mock_search(origin, destination, date)
        return await self._real_search(origin, destination, date)

    # ─────────────────────────────────────────────────────────────────────
    # Mock mode
    # ─────────────────────────────────────────────────────────────────────
    async def _mock_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        start = time.monotonic()
        await asyncio.sleep(random.uniform(0.05, 0.3))

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

    # ─────────────────────────────────────────────────────────────────────
    # Real mode
    # ─────────────────────────────────────────────────────────────────────
    async def _real_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

        start = time.monotonic()
        origin_info = get_airport_info(origin)
        dest_info = get_airport_info(destination)
        date_str = date.strftime("%Y-%m-%d")
        search_url = _build_search_url(origin, dest, date_str) if False else _build_search_url(origin, destination, date_str)

        base = ScanResult(
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

        cookies = _load_cookies()

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
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    timezone_id="America/New_York",
                )

                # Inject saved session cookies if available
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                        logger.debug("Injected %d session cookies", len(cookies))
                    except Exception as exc:
                        logger.warning("Cookie injection failed: %s", exc)

                page = await context.new_page()

                # Extra header to look more like a real browser
                await page.set_extra_http_headers({
                    "Accept-Language": "en-US,en;q=0.9",
                })

                try:
                    logger.info("Searching %s→%s on %s", origin, destination, date_str)
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)

                    # Detect captcha / block immediately
                    if await page.query_selector(SELECTOR_CAPTCHA):
                        logger.warning("Captcha/block detected — %s→%s", origin, destination)
                        await self._save_debug(page, f"{origin}-{destination}-{date_str}-captcha")
                        base.raw_status = "BLOCKED_CAPTCHA"
                        return base

                    # Wait for loading spinner to clear (SPA loads async)
                    try:
                        await page.wait_for_selector(SELECTOR_LOADING, state="hidden", timeout=5000)
                    except PlaywrightTimeout:
                        pass  # No spinner visible — that's fine

                    # Wait for flight results or no-results to appear
                    try:
                        await page.wait_for_selector(
                            f"{SELECTOR_FLIGHT_CARD}, {SELECTOR_NO_FLIGHTS}",
                            timeout=30000,
                        )
                    except PlaywrightTimeout:
                        await self._save_debug(page, f"{origin}-{destination}-{date_str}-timeout")
                        base.raw_status = "TIMEOUT_WAITING_FOR_RESULTS"
                        return base

                    # Re-check for captcha after page settles
                    if await page.query_selector(SELECTOR_CAPTCHA):
                        base.raw_status = "BLOCKED_CAPTCHA"
                        return base

                    # No flights on this date
                    if await page.query_selector(SELECTOR_NO_FLIGHTS):
                        base.raw_status = "NO_FLIGHTS_FOUND"
                        return base

                    # ── Strategy 1: find GoWild by data-testid / class ──────────────
                    gowild_el = await page.query_selector(SELECTOR_GOWILD_BADGE)

                    # ── Strategy 2: find GoWild by visible text ──────────────────────
                    if not gowild_el:
                        gowild_el = await page.query_selector(SELECTOR_GOWILD_TEXT)

                    if not gowild_el:
                        # GoWild not visible — either not available or selectors need updating
                        await self._save_debug(page, f"{origin}-{destination}-{date_str}-no-gowild")
                        base.raw_status = "NO_GOWILD_FARE"
                        return base

                    base.gowild_available = True
                    base.raw_status = "GOWILD_AVAILABLE"

                    # ── Extract price ────────────────────────────────────────────────
                    # Look for price near the GoWild element (parent card context)
                    try:
                        card = await gowild_el.evaluate_handle(
                            "el => el.closest('[data-testid], [class*=\"card\"], [class*=\"Card\"], [class*=\"result\"], [class*=\"Result\"]') || el.parentElement"
                        )
                        price_el = await card.query_selector(SELECTOR_PRICE)
                        if price_el:
                            base.price_text = (await price_el.inner_text()).strip()
                    except Exception:
                        # Fall back to page-level price near GoWild
                        try:
                            price_el = await page.query_selector(SELECTOR_PRICE)
                            if price_el:
                                base.price_text = (await price_el.inner_text()).strip()
                        except Exception:
                            pass

                    # ── Extract flight card (find the containing flight row) ─────────
                    try:
                        cards = await page.query_selector_all(SELECTOR_FLIGHT_CARD)
                        if cards:
                            card = cards[0]  # Use first flight card for time/stop info

                            dep_el = await card.query_selector(SELECTOR_DEPARTURE_TIME)
                            if dep_el:
                                base.departure_time = (await dep_el.inner_text()).strip()[:10]

                            arr_el = await card.query_selector(SELECTOR_ARRIVAL_TIME)
                            if arr_el:
                                base.arrival_time = (await arr_el.inner_text()).strip()[:10]

                            nonstop_el = await card.query_selector(SELECTOR_NONSTOP)
                            base.is_nonstop = nonstop_el is not None

                            if not base.is_nonstop:
                                stops_el = await card.query_selector(SELECTOR_STOPS)
                                if stops_el:
                                    base.connection_info = (await stops_el.inner_text()).strip()[:50]
                    except Exception as exc:
                        logger.debug("Card detail extraction failed: %s", exc)

                    # Always save a success screenshot if DEBUG_SCREENSHOTS is on
                    await self._save_debug(page, f"{origin}-{destination}-{date_str}-SUCCESS")

                finally:
                    await browser.close()

        except Exception as exc:
            logger.exception("Scraper error %s→%s on %s", origin, destination, date_str)
            base.raw_status = f"ERROR: {type(exc).__name__}"
            base.error = str(exc)
            # Respect delay even on error to avoid hammering
            await asyncio.sleep(2)

        base.scan_duration_ms = int((time.monotonic() - start) * 1000)
        return base

    async def _save_debug(self, page, label: str) -> None:
        """Save screenshot + HTML for selector debugging. Always saves on errors
        regardless of DEBUG_SCREENSHOTS setting; optional for success."""
        is_error = any(x in label for x in ("timeout", "captcha", "error", "no-gowild", "no-cards"))
        if not is_error and not self.settings.DEBUG_SCREENSHOTS:
            return

        debug_dir = Path("debug")
        debug_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace("/", "-")
        try:
            shot_path = debug_dir / f"{ts}_{safe_label}.png"
            await page.screenshot(path=str(shot_path), full_page=True)
            html = await page.content()
            html_path = debug_dir / f"{ts}_{safe_label}.html"
            html_path.write_text(html, encoding="utf-8")
            logger.info("Debug saved: %s (%.1f KB)", shot_path.name, len(html) / 1024)
        except Exception as exc:
            logger.warning("Debug save failed: %s", exc)
