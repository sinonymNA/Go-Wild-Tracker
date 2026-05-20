from __future__ import annotations

"""
GoWild Scraper — Frontier Airlines GoWild availability via their booking API.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW IT WORKS (no Playwright needed!)

Frontier's booking site at booking.flyfrontier.com/Flight/InternalSelect
returns server-rendered HTML containing embedded JSON flight data inside
a <script type="text/javascript"> tag. We parse that JSON directly.

Key JSON fields:
  flight["isGoWildFareEnabled"]       → True/False (GoWild available)
  flight["goWildFare"]                → price as float (taxes/fees)
  flight["goWildFareSeatsRemaining"]  → seats left (may be None)
  flight["stopsText"]                 → "Nonstop" or "1 Stop via DEN"
  flight["duration"]                  → "2h 30m"
  flight["legs"][0]["departureDateFormatted"]  → "8:30 AM"
  flight["legs"][0]["departureDate"]           → ISO datetime string

OPTIONAL: If GoWild fares require a logged-in session, export your
browser cookies to data/frontier_cookies.json and the scraper will
include them automatically.

MOCK MODE: Set MOCK_MODE=true to generate fake data without hitting
Frontier's servers — useful for UI development and deployment testing.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import datetime
import html as html_module
import json
import logging
import random
import re
import time
from pathlib import Path

import httpx

from app.airport_data import get_airport_info
from app.config import get_settings
from app.models import ScanResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# API endpoints (discovered from community GoWild scrapers)
# ─────────────────────────────────────────────────────────────────────────────

# Main flight search — returns HTML with embedded JSON flight data
INTERNAL_SELECT_URL = "https://booking.flyfrontier.com/Flight/InternalSelect"

# Schedule check — returns disabled dates for a route (useful for skipping
# dates with no flights at all before running the more expensive InternalSelect)
RETRIEVE_SCHEDULE_URL = "https://booking.flyfrontier.com/Flight/RetrieveSchedule"


def _format_date(date: datetime.date) -> str:
    """Format date as 'Jul%2015,%202026' — Frontier's expected query param format."""
    # strftime %d zero-pads (05, 15) which Frontier accepts
    return date.strftime("%b-%d,-%Y").replace("-", "%20")


def _build_search_url(origin: str, dest: str, date: datetime.date) -> str:
    return (
        f"{INTERNAL_SELECT_URL}"
        f"?o1={origin}&d1={dest}&dd1={_format_date(date)}&ADT=1&mon=true&promo="
    )


def _build_schedule_url(origin: str, dest: str) -> str:
    return (
        f"{RETRIEVE_SCHEDULE_URL}"
        f"?calendarSelectableDays.Origin={origin}"
        f"&calendarSelectableDays.Destination={dest}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# User-Agent pool — rotate to look natural
# ─────────────────────────────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ─────────────────────────────────────────────────────────────────────────────
# Cookie session import
# Save your Frontier browser session to this file to include auth cookies.
# Export with the "Cookie-Editor" Chrome extension → Export → save JSON here.
# ─────────────────────────────────────────────────────────────────────────────
COOKIES_FILE = Path("data/frontier_cookies.json")


def _load_cookies() -> dict[str, str]:
    """Load browser-exported cookies as a name→value dict for httpx."""
    if not COOKIES_FILE.exists():
        return {}
    try:
        raw = json.loads(COOKIES_FILE.read_text())
        # Cookie-Editor exports: list of {name, value, domain, ...}
        # Plain dict format also supported
        if isinstance(raw, list):
            result = {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
        elif isinstance(raw, dict) and "cookies" in raw:
            result = {c["name"]: c["value"] for c in raw["cookies"] if "name" in c}
        elif isinstance(raw, dict):
            result = raw  # Already name→value
        else:
            result = {}
        logger.info("Loaded %d session cookies from %s", len(result), COOKIES_FILE)
        return result
    except Exception as exc:
        logger.warning("Failed to load cookies from %s: %s", COOKIES_FILE, exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# HTML/JSON parsing
# ─────────────────────────────────────────────────────────────────────────────

def _extract_flight_json(html_text: str) -> dict | None:
    """
    Pull the embedded flight JSON out of Frontier's server-rendered HTML.

    The page contains a <script type="text/javascript"> tag whose content is:
        {...flight data JSON...};
    We find the first { and extract up to the first ; to get the JSON object.
    """
    # Find the script tag
    match = re.search(
        r'<script[^>]+type=["\']text/javascript["\'][^>]*>(.*?)</script>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        # Try without type attribute (some responses may vary)
        match = re.search(r'<script[^>]*>(.*?)</script>', html_text, re.DOTALL | re.IGNORECASE)
    if not match:
        logger.debug("No <script> tag found in response")
        return None

    content = html_module.unescape(match.group(1).strip())

    brace = content.find("{")
    if brace == -1:
        logger.debug("No JSON object found in script content")
        return None

    content = content[brace:]
    semi = content.find(";")
    if semi > 0:
        content = content[:semi]

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.debug("JSON parse failed: %s — snippet: %s", exc, content[:200])
        return None


def _parse_flights(data: dict, origin: str, dest: str, date: datetime.date) -> ScanResult | None:
    """
    Parse the JSON flight data and return a ScanResult.
    Returns None if the JSON structure is unexpected.
    """
    origin_info = get_airport_info(origin)
    dest_info = get_airport_info(dest)

    base = dict(
        origin=origin,
        destination=dest,
        origin_lat=origin_info["lat"] if origin_info else None,
        origin_lon=origin_info["lon"] if origin_info else None,
        dest_lat=dest_info["lat"] if dest_info else None,
        dest_lon=dest_info["lon"] if dest_info else None,
        departure_date=date,
        gowild_available=False,
        is_nonstop=False,
        raw_status="NO_GOWILD_FARE",
    )

    try:
        journeys = data.get("journeys") or []
        if not journeys:
            base["raw_status"] = "NO_FLIGHTS_FOUND"
            return ScanResult(**base)

        flights = journeys[0].get("flights") or []
        if not flights:
            base["raw_status"] = "NO_FLIGHTS_FOUND"
            return ScanResult(**base)

        for flight in flights:
            if not flight.get("isGoWildFareEnabled"):
                continue

            # Found a GoWild fare!
            leg = (flight.get("legs") or [{}])[0]
            stops_text = flight.get("stopsText", "")
            is_nonstop = "nonstop" in stops_text.lower()
            connection_info = None if is_nonstop else stops_text[:50]

            price = flight.get("goWildFare")
            price_text = f"${price:.2f} taxes+fees" if price is not None else "GoWild"
            seats = flight.get("goWildFareSeatsRemaining")
            if seats is not None:
                price_text += f" ({seats} seats)"

            dep_time = leg.get("departureDateFormatted", "")
            arr_time = leg.get("arrivalDateFormatted", "")

            # Parse departure time into HH:MM 24h format
            dep_24 = _parse_ampm(dep_time)
            arr_24 = _parse_ampm(arr_time)

            base.update(
                gowild_available=True,
                raw_status="GOWILD_AVAILABLE",
                price_text=price_text,
                departure_time=dep_24 or dep_time[:10],
                arrival_time=arr_24 or arr_time[:10],
                is_nonstop=is_nonstop,
                connection_info=connection_info,
            )
            return ScanResult(**base)

        # No GoWild fare found
        return ScanResult(**base)

    except Exception as exc:
        logger.warning("Flight JSON parse error for %s→%s: %s", origin, dest, exc)
        base["raw_status"] = f"PARSE_ERROR: {exc}"
        base["error"] = str(exc)
        return ScanResult(**base)


def _parse_ampm(time_str: str) -> str | None:
    """Convert '8:30 AM' → '08:30', '11:00 PM' → '23:00'. Returns None if unparseable."""
    if not time_str:
        return None
    try:
        t = datetime.datetime.strptime(time_str.strip(), "%I:%M %p")
        return t.strftime("%H:%M")
    except ValueError:
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
# Scraper
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

    # ── Mock mode ──────────────────────────────────────────────────────────
    async def _mock_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        start = time.monotonic()
        await asyncio.sleep(random.uniform(0.05, 0.25))

        rng = random.Random(f"{origin}-{destination}-{date}")
        available = rng.random() < 0.55

        prices = [5.60, 11.20, 0.00, 16.80]
        dep_times = ["06:15", "08:30", "10:45", "13:00", "15:20", "17:50", "19:10", "21:30"]
        duration_min = rng.randint(90, 300)
        dep_time = rng.choice(dep_times)
        dep_dt = datetime.datetime.strptime(dep_time, "%H:%M")
        arr_time = (dep_dt + datetime.timedelta(minutes=duration_min)).strftime("%H:%M")

        is_nonstop = frozenset({origin, destination}) in _NONSTOP_PAIRS or rng.random() < 0.35
        connection_info = None if is_nonstop else rng.choice(_MOCK_HUBS)

        origin_info = get_airport_info(origin)
        dest_info = get_airport_info(destination)
        elapsed_ms = int((time.monotonic() - start) * 1000) + rng.randint(200, 800)

        price = rng.choice(prices)
        seats = rng.randint(1, 6)

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
            price_text=f"${price:.2f} taxes+fees ({seats} seats)" if available else None,
            raw_status="MOCK_AVAILABLE" if available else "MOCK_UNAVAILABLE",
            scan_duration_ms=elapsed_ms,
            error=None,
        )

    # ── Real mode — direct HTTP API (no browser needed!) ──────────────────
    async def _real_search(
        self, origin: str, destination: str, date: datetime.date
    ) -> ScanResult:
        start = time.monotonic()
        origin_info = get_airport_info(origin)
        dest_info = get_airport_info(destination)

        error_base = dict(
            origin=origin,
            destination=destination,
            origin_lat=origin_info["lat"] if origin_info else None,
            origin_lon=origin_info["lon"] if origin_info else None,
            dest_lat=dest_info["lat"] if dest_info else None,
            dest_lon=dest_info["lon"] if dest_info else None,
            departure_date=date,
            gowild_available=False,
            is_nonstop=False,
        )

        url = _build_search_url(origin, destination, date)
        cookies = _load_cookies()
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://booking.flyfrontier.com/",
        }

        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                headers=headers,
                follow_redirects=True,
                timeout=30.0,
            ) as client:
                logger.info("Fetching %s→%s on %s", origin, destination, date)
                response = await client.get(url)

            if response.status_code == 403:
                logger.warning("403 Forbidden for %s→%s — may need session cookies", origin, destination)
                result = ScanResult(**error_base, raw_status="BLOCKED_403")
                result.error = "HTTP 403 — try adding session cookies to data/frontier_cookies.json"
                result.scan_duration_ms = int((time.monotonic() - start) * 1000)
                return result

            if response.status_code != 200:
                result = ScanResult(**error_base, raw_status=f"HTTP_{response.status_code}")
                result.scan_duration_ms = int((time.monotonic() - start) * 1000)
                return result

            # Parse the embedded JSON from the HTML response
            data = _extract_flight_json(response.text)

            if data is None:
                # Save raw response for debugging
                _save_debug_response(
                    response.text,
                    f"{origin}-{destination}-{date}-parse_failed",
                )
                result = ScanResult(**error_base, raw_status="JSON_PARSE_FAILED")
                result.error = "Could not extract JSON from response — check debug/ for raw HTML"
                result.scan_duration_ms = int((time.monotonic() - start) * 1000)
                return result

            result = _parse_flights(data, origin, destination, date)
            if result is None:
                result = ScanResult(**error_base, raw_status="UNEXPECTED_JSON_STRUCTURE")
                result.scan_duration_ms = int((time.monotonic() - start) * 1000)
                return result

            result.scan_duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "%s→%s %s — GoWild=%s price=%s",
                origin, destination, date,
                result.gowild_available, result.price_text,
            )
            return result

        except httpx.TimeoutException:
            result = ScanResult(**error_base, raw_status="TIMEOUT")
            result.error = "Request timed out after 30s"
            result.scan_duration_ms = int((time.monotonic() - start) * 1000)
            return result

        except Exception as exc:
            logger.exception("Scraper error %s→%s on %s", origin, destination, date)
            result = ScanResult(**error_base, raw_status=f"ERROR: {type(exc).__name__}")
            result.error = str(exc)
            result.scan_duration_ms = int((time.monotonic() - start) * 1000)
            return result


# ─────────────────────────────────────────────────────────────────────────────
# Debug helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_debug_response(html_text: str, label: str) -> None:
    """Save raw HTML response to debug/ for inspection when parsing fails."""
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = label.replace("/", "-").replace(" ", "_")
    path = debug_dir / f"{ts}_{safe}.html"
    path.write_text(html_text, encoding="utf-8")
    logger.info("Debug HTML saved: %s (%d bytes)", path.name, len(html_text))


# ─────────────────────────────────────────────────────────────────────────────
# Schedule helper (optional — check which dates have flights before scanning)
# ─────────────────────────────────────────────────────────────────────────────

async def get_disabled_dates(origin: str, dest: str) -> set[datetime.date]:
    """
    Fetch Frontier's schedule data for a route to find dates with no flights.
    Returns a set of disabled (no-flight) dates so we can skip them.
    """
    url = _build_schedule_url(origin, dest)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
            )
        if resp.status_code != 200:
            return set()
        data = _extract_flight_json(resp.text)
        if not data:
            return set()
        disabled_raw = data.get("calendarSelectableDays", {}).get("disabledDates") or []
        disabled: set[datetime.date] = set()
        for d in disabled_raw:
            try:
                disabled.add(datetime.date.fromisoformat(d[:10]))
            except (ValueError, TypeError):
                pass
        return disabled
    except Exception as exc:
        logger.debug("RetrieveSchedule failed for %s→%s: %s", origin, dest, exc)
        return set()
