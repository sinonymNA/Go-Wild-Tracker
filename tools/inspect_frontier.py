#!/usr/bin/env python3
"""
Frontier GoWild Inspection Tool
=================================
Run this LOCALLY (not on Railway/cloud) to capture Frontier's real DOM
structure so the selectors in app/scraper.py can be tuned.

PREREQUISITES (local machine only):
    pip install playwright
    playwright install chromium

USAGE:
    # Inspect all candidate URLs for ATL→DEN on a specific date
    python tools/inspect_frontier.py --origin ATL --dest DEN --date 2025-08-15

    # Inspect a single specific URL (once you know which URL works)
    python tools/inspect_frontier.py --url "https://booking.flyfrontier.com/flights?..."

    # Run headless (no visible browser window)
    python tools/inspect_frontier.py --origin ATL --dest DEN --date 2025-08-15 --headless

    # Load your Frontier session cookies first (to see GoWild fares as a pass holder)
    # 1. Export cookies with browser extension (see README)
    # 2. Save as data/frontier_cookies.json
    # 3. python tools/inspect_frontier.py --origin ATL --dest DEN --date 2025-08-15 --cookies data/frontier_cookies.json

OUTPUT:
    debug/  ← screenshots + saved HTML files
    debug/inspection_report.json  ← full structured report

AFTER RUNNING:
    1. Open debug/*.png to see which URL showed flight results
    2. Open the matching *_page.html in Chrome → right-click a flight card → Inspect
    3. Find the real CSS selectors for GoWild badge, price, times, stops
    4. Update SELECTOR_* constants in app/scraper.py
    5. Set MOCK_MODE=false and TEST with a single route first
"""

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path


# ─── URL candidates ─────────────────────────────────────────────────────────
def build_urls(origin: str, dest: str, date: str) -> list[tuple[str, str]]:
    return [
        (
            "01_booking_direct",
            f"https://booking.flyfrontier.com/flights"
            f"?origin={origin}&destination={dest}"
            f"&departureDate={date}&adults=1&tripType=OW&currency=USD",
        ),
        (
            "02_booking_oneway",
            f"https://booking.flyfrontier.com/flights/oneway"
            f"?origin={origin}&destination={dest}&date={date}&adults=1",
        ),
        (
            "03_main_search",
            f"https://www.flyfrontier.com/plan-trip/book-trip/flight-search/"
            f"#/flight-search/one-way?from={origin}&to={dest}&date={date}",
        ),
        (
            "04_low_fare_calendar",
            f"https://www.flyfrontier.com/plan-trip/low-fare-calendar/"
            f"?from={origin}&to={dest}",
        ),
        (
            "05_homepage",
            "https://www.flyfrontier.com/",
        ),
    ]


# ─── Selector probes ─────────────────────────────────────────────────────────
PROBES: dict[str, list[str]] = {
    "flight_cards": [
        "[data-testid='flight-result']",
        "[data-testid='flight-card']",
        "[data-testid='itinerary-card']",
        "[data-testid*='flight-option']",
        "[data-testid*='flight-row']",
        "[data-testid*='itinerary']",
        "[class*='FlightResult']",
        "[class*='flight-result']",
        "[class*='ItineraryCard']",
        "[class*='itinerary-card']",
        "[class*='FlightCard']",
        "[class*='flight-card']",
        "[class*='SearchResult']",
    ],
    "gowild": [
        "[data-testid*='gowild']",
        "[data-testid*='GoWild']",
        "[data-testid*='wild']",
        "[aria-label*='GoWild']",
        "[aria-label*='Go Wild']",
        "[class*='GoWild']",
        "[class*='gowild']",
        "[class*='go-wild']",
        # Text-based
        "button:has-text('GoWild')",
        "div:has-text('GoWild')",
        "span:has-text('GoWild')",
    ],
    "prices": [
        "[data-testid*='price']",
        "[data-testid*='fare-amount']",
        "[data-testid*='amount']",
        "[data-testid*='total']",
        "[class*='Price']",
        "[class*='price']",
        "[class*='FareAmount']",
        "[class*='fare-amount']",
        "[class*='Amount']",
    ],
    "departure_time": [
        "[data-testid*='departure-time']",
        "[data-testid*='depart-time']",
        "[aria-label*='Departs']",
        "[class*='DepartureTime']",
        "[class*='departure-time']",
        "[class*='depart-time']",
        "[class*='DepartTime']",
    ],
    "arrival_time": [
        "[data-testid*='arrival-time']",
        "[data-testid*='arrive-time']",
        "[aria-label*='Arrives']",
        "[class*='ArrivalTime']",
        "[class*='arrival-time']",
        "[class*='arrive-time']",
    ],
    "nonstop": [
        "[data-testid*='nonstop']",
        "[data-testid*='stops']",
        "[class*='Nonstop']",
        "[class*='nonstop']",
        "[class*='StopCount']",
        "[class*='stop-count']",
        "span:has-text('Nonstop')",
        "span:has-text('nonstop')",
    ],
    "no_results": [
        "[data-testid*='no-result']",
        "[data-testid*='empty']",
        "[class*='NoResult']",
        "[class*='EmptyState']",
        "[class*='no-result']",
        "div:has-text('No flights')",
        "div:has-text('No results')",
    ],
    "loading": [
        "[data-testid*='loading']",
        "[class*='Loading']",
        "[class*='Spinner']",
        "[class*='spinner']",
        "[role='progressbar']",
    ],
    "captcha": [
        "[id*='captcha']",
        "[class*='captcha']",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "[class*='challenge']",
    ],
    "search_form": [
        "[data-testid*='origin']",
        "[data-testid*='from']",
        "[placeholder*='From']",
        "[aria-label*='From']",
        "[aria-label*='Origin']",
        "input[name*='origin']",
    ],
    "fare_tabs": [
        "[data-testid*='fare-tab']",
        "[data-testid*='fare-type']",
        "[role='tab']",
        "[class*='FareTab']",
        "[class*='fare-tab']",
        "[class*='FareType']",
    ],
}


async def probe_page(page) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for category, selectors in PROBES.items():
        found = []
        for sel in selectors:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    found.append({"selector": sel, "count": count})
            except Exception:
                pass
        results[category] = found
    return results


async def get_relevant_classes(page) -> list[str]:
    try:
        classes: list[str] = await page.evaluate("""() => {
            const s = new Set();
            document.querySelectorAll('*').forEach(el => {
                el.classList.forEach(c => { if (c.length > 3) s.add(c); });
            });
            return [...s].sort();
        }""")
        keywords = [
            "flight","fare","price","wild","gowild","card","result","itinerary",
            "depart","arriv","stop","nonstop","time","amount","avail","tab",
            "segment","route","schedule","duration","connect"
        ]
        return [c for c in classes if any(k in c.lower() for k in keywords)]
    except Exception:
        return []


async def get_data_testids(page) -> list[str]:
    """Extract all data-testid attribute values — these are the most stable selectors."""
    try:
        ids: list[str] = await page.evaluate("""() => {
            const ids = new Set();
            document.querySelectorAll('[data-testid]').forEach(el => {
                ids.add(el.getAttribute('data-testid'));
            });
            return [...ids].sort();
        }""")
        return ids
    except Exception:
        return []


async def get_gowild_context(page) -> str:
    """Extract HTML around any GoWild text for selector analysis."""
    try:
        return await page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while (node = walker.nextNode()) {
                if (node.textContent.includes('GoWild') || node.textContent.includes('Go Wild')) {
                    // Return the parent element's outerHTML (up to 2000 chars)
                    let el = node.parentElement;
                    for (let i = 0; i < 4; i++) {
                        if (el.parentElement) el = el.parentElement;
                    }
                    return el.outerHTML.slice(0, 3000);
                }
            }
            return null;
        }""")
    except Exception:
        return ""


async def intercept_requests(page) -> list[dict]:
    requests = []
    def on_req(req):
        url = req.url
        if any(k in url.lower() for k in ["flight","search","fare","avail","wild","booking","api"]):
            requests.append({"method": req.method, "url": url[:250]})
    page.on("request", on_req)
    return requests


async def inspect_url(
    pw, url: str, label: str, headless: bool, debug_dir: Path, cookies: list | None
) -> dict:
    print(f"\n{'─'*60}")
    print(f"▶ {label}")
    print(f"  {url[:80]}")
    print(f"{'─'*60}")

    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1400, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    if cookies:
        try:
            await context.add_cookies(cookies)
            print(f"  Cookies loaded: {len(cookies)}")
        except Exception as e:
            print(f"  Cookie load failed: {e}")

    page = await context.new_page()
    await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
    api_requests = await intercept_requests(page)

    result = {
        "label": label, "url": url, "status": "unknown",
        "final_url": url, "title": "", "http_status": None,
        "probes": {}, "classes": [], "testids": [], "gowild_html": "",
        "api_calls": [], "screenshots": [], "html_path": "",
    }

    try:
        print("  → Navigating…")
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        result["http_status"] = resp.status if resp else None
        result["final_url"] = page.url
        result["title"] = await page.title()
        print(f"  HTTP {result['http_status']} | \"{result['title'][:50]}\"")
        print(f"  Final URL: {result['final_url'][:80]}")

        # Initial screenshot
        s1 = debug_dir / f"{label}_1_initial.png"
        await page.screenshot(path=str(s1), full_page=True)
        result["screenshots"].append(str(s1))
        print(f"  Screenshot: {s1.name}")

        # Wait for SPA to render
        print("  → Waiting 6s for SPA render…")
        await asyncio.sleep(6)

        # Dismiss any cookie banners / modals
        for dismiss in ["button:has-text('Accept')", "button:has-text('Got it')",
                        "button:has-text('OK')", "[aria-label='Close']"]:
            try:
                el = await page.query_selector(dismiss)
                if el and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Wait more for flight results
        await asyncio.sleep(4)

        # Final screenshot
        s2 = debug_dir / f"{label}_2_loaded.png"
        await page.screenshot(path=str(s2), full_page=True)
        result["screenshots"].append(str(s2))

        # Run all probes
        result["probes"] = await probe_page(page)
        result["classes"] = await get_relevant_classes(page)
        result["testids"] = await get_data_testids(page)
        result["gowild_html"] = await get_gowild_context(page) or ""
        result["api_calls"] = api_requests[:]

        # Print probe summary
        print("\n  Selector probes:")
        useful = 0
        for cat, found in result["probes"].items():
            if found:
                useful += 1
                print(f"    ✓ {cat}: {found[0]['selector']} × {found[0]['count']}")
                for f in found[1:2]:
                    print(f"       also: {f['selector']} × {f['count']}")

        if useful == 0:
            print("    ✗ Nothing found — page may not have loaded results")

        if result["testids"]:
            flight_ids = [i for i in result["testids"] if any(
                k in i.lower() for k in ["flight","fare","price","gowild","wild","time","stop","depart","arriv"]
            )]
            if flight_ids:
                print(f"\n  Relevant data-testids:")
                for tid in flight_ids[:20]:
                    print(f"    [data-testid='{tid}']")

        if result["gowild_html"]:
            print(f"\n  ✓ Found 'GoWild' text on page!")
            print(f"  HTML context (first 500 chars):\n    {result['gowild_html'][:500]}")
        else:
            print("\n  ✗ 'GoWild' text not found on page")

        if result["api_calls"]:
            print(f"\n  API calls ({len(result['api_calls'])}):")
            for r in result["api_calls"][:5]:
                print(f"    {r['method']} {r['url'][:80]}")

        # Save HTML
        html = await page.content()
        html_path = debug_dir / f"{label}_page.html"
        html_path.write_text(html, encoding="utf-8")
        result["html_path"] = str(html_path)
        print(f"\n  HTML saved: {html_path.name} ({len(html):,} bytes)")

    except Exception as exc:
        result["status"] = f"ERROR: {exc}"
        print(f"  ERROR: {exc}")
        try:
            err_shot = debug_dir / f"{label}_error.png"
            await page.screenshot(path=str(err_shot))
            result["screenshots"].append(str(err_shot))
        except Exception:
            pass
    finally:
        await browser.close()

    return result


async def main():
    p = argparse.ArgumentParser(description="Inspect Frontier flight search DOM")
    p.add_argument("--origin", default="ATL")
    p.add_argument("--dest", default="DEN")
    p.add_argument(
        "--date",
        default=(datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d"),
    )
    p.add_argument("--headless", action="store_true")
    p.add_argument("--url", help="Inspect a single specific URL")
    p.add_argument("--cookies", help="Path to cookies JSON file (exported from browser)")
    args = p.parse_args()

    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)

    # Load cookies
    cookies = None
    cookies_path = Path(args.cookies) if args.cookies else Path("data/frontier_cookies.json")
    if cookies_path.exists():
        try:
            raw = json.loads(cookies_path.read_text())
            cookies = raw if isinstance(raw, list) else raw.get("cookies", [])
            print(f"Loaded {len(cookies)} cookies from {cookies_path}")
        except Exception as e:
            print(f"Warning: could not load cookies: {e}")

    print(f"\n{'#'*60}")
    print("# Frontier DOM Inspection")
    print(f"# Route:  {args.origin} → {args.dest}")
    print(f"# Date:   {args.date}")
    print(f"# Mode:   {'headless' if args.headless else 'VISIBLE BROWSER'}")
    print(f"# Output: debug/")
    print(f"{'#'*60}")

    if not args.headless:
        print("\nNote: A browser window will open. Watch what loads.")
        print("If Frontier shows a search form, that URL doesn't deep-link — try another.")

    urls = [(f"custom_{i}", args.url)] if args.url else build_urls(args.origin, args.dest, args.date)

    from playwright.async_api import async_playwright

    all_results = []
    async with async_playwright() as pw:
        for label, url in urls:
            r = await inspect_url(pw, url, label, args.headless, debug_dir, cookies)
            all_results.append(r)
            await asyncio.sleep(3)

    # Write report
    report = debug_dir / "inspection_report.json"
    report.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")

    # ── Final summary ────────────────────────────────────────────────────────
    print(f"\n\n{'#'*60}")
    print("# SUMMARY")
    print(f"{'#'*60}\n")

    best = None
    for r in all_results:
        has_flights = bool(r["probes"].get("flight_cards") or r["probes"].get("gowild"))
        has_gowild = bool(r["gowild_html"])
        flag = "✓✓ GoWild found!" if has_gowild else ("✓ Flight cards found" if has_flights else "✗ No results")
        print(f"[{r['label']}]  HTTP={r['http_status']}  {flag}")
        if has_gowild or has_flights:
            best = r

    if best:
        print(f"\n✅ BEST URL: {best['url']}")
        print("\nRECOMMENDED SELECTORS to try in app/scraper.py:")
        for cat, found in best["probes"].items():
            if found:
                print(f"  {cat}: \"{found[0]['selector']}\"")
    else:
        print("\n⚠  No URL produced flight results.")
        print("   Options:")
        print("   1. The date may have no flights — try a different date 30-60 days out")
        print("   2. Frontier may require login to see GoWild fares — export cookies and rerun")
        print("   3. Frontier's SPA may need more time — try increasing sleep from 6 to 12s")

    print(f"\n{'#'*60}")
    print("# FILES SAVED")
    print(f"{'#'*60}")
    for r in all_results:
        for s in r["screenshots"]:
            print(f"  {Path(s).name}")
        if r["html_path"]:
            print(f"  {Path(r['html_path']).name}")
    print(f"  inspection_report.json")

    print(f"\n{'#'*60}")
    print("# NEXT STEPS")
    print(f"{'#'*60}")
    print("""
  1. Open debug/*.png — see which URL showed flight cards
  2. Open the matching *_page.html in Chrome
  3. Press F12 → right-click a GoWild fare option → Inspect
  4. Note the data-testid attributes and class names
  5. Update SELECTOR_* constants in app/scraper.py
  6. If GoWild fares require login:
       a. Log into flyfrontier.com in Chrome
       b. Install "Cookie-Editor" Chrome extension
       c. Click Export → copy JSON
       d. Save to data/frontier_cookies.json
       e. Rerun: python tools/inspect_frontier.py --origin ATL --dest DEN --date {date}
  7. Test a single route: set MOCK_MODE=false, trigger scan from dashboard
""")


if __name__ == "__main__":
    asyncio.run(main())
