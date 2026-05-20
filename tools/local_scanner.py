#!/usr/bin/env python3
"""
Local Scanner — run GoWild scans from your home machine.

Frontier's CDN blocks datacenter IPs (Railway, AWS, etc.) but allows
residential IPs. Run this script locally to scan routes, then optionally
push results to a remote GoWild Radar instance via its API.

USAGE:
    # Scan and save to local DB (DATABASE_URL in .env)
    python tools/local_scanner.py

    # Scan specific routes
    python tools/local_scanner.py --origin ATL --dests DEN LAS MCO

    # Scan and post results to a remote Railway deployment
    python tools/local_scanner.py --remote https://your-app.railway.app --password yourpass

    # Scan just today and tomorrow (GoWild books 24h ahead anyway)
    python tools/local_scanner.py --days 2

SETUP:
    pip install -r requirements.txt
    cp .env.example .env
    # Edit .env: set ADMIN_PASSWORD, MOCK_MODE=false, DATABASE_URL
"""

import argparse
import asyncio
import datetime
import os
import sys
from pathlib import Path

# Make sure app/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("ADMIN_PASSWORD", "local")


async def scan_locally(origin: str, dests: list[str], days: int) -> None:
    """Run scans and save to local database."""
    from app.database import AsyncSessionLocal, init_db
    from app.scraper import GoWildScraper, get_disabled_dates
    from app.alerts import check_and_send_alerts

    await init_db()

    scraper = GoWildScraper()
    scan_dates = [datetime.date.today() + datetime.timedelta(days=i) for i in range(days)]

    async with AsyncSessionLocal() as db:
        results = []
        for dest in dests:
            print(f"\n{'─'*40}")
            print(f"Scanning {origin} → {dest}")

            # Skip no-service dates
            disabled = await get_disabled_dates(origin, dest)
            active_dates = [d for d in scan_dates if d not in disabled]
            if disabled:
                print(f"  Skipping {len(disabled)} dates with no service")

            for date in active_dates:
                result = await scraper.search_route(origin, dest, date)
                db.add(result)
                await db.flush()
                await db.commit()
                results.append(result)

                status = "✓ AVAILABLE" if result.gowild_available else "  unavailable"
                price = f"  {result.price_text}" if result.price_text else ""
                print(f"  {date}: {status}{price}")

        await check_and_send_alerts(db, results)

    available = [r for r in results if r.gowild_available]
    print(f"\n{'='*40}")
    print(f"Scan complete: {len(results)} routes checked")
    print(f"GoWild available: {len(available)}")
    if available:
        print("\nAvailable flights:")
        for r in available:
            print(f"  {r.origin}→{r.destination} {r.departure_date} {r.departure_time} {r.price_text}")


async def scan_and_post(origin: str, dests: list[str], days: int, remote: str, password: str) -> None:
    """Scan locally and POST results to a remote GoWild Radar instance."""
    import httpx

    # Login to get session cookie
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        login_resp = await client.post(
            f"{remote.rstrip('/')}/login",
            data={"password": password},
        )
        if login_resp.status_code not in (200, 303):
            print(f"Login failed: HTTP {login_resp.status_code}")
            return
        cookies = dict(client.cookies)
        print(f"Logged in to {remote}")

    from app.scraper import GoWildScraper, get_disabled_dates

    scraper = GoWildScraper()
    scan_dates = [datetime.date.today() + datetime.timedelta(days=i) for i in range(days)]

    all_results = []
    for dest in dests:
        print(f"\n{'─'*40}")
        print(f"Scanning {origin} → {dest}")
        disabled = await get_disabled_dates(origin, dest)
        active_dates = [d for d in scan_dates if d not in disabled]

        for date in active_dates:
            result = await scraper.search_route(origin, dest, date)
            all_results.append(result)
            status = "✓ AVAILABLE" if result.gowild_available else "  unavailable"
            print(f"  {date}: {status}")

    # POST results to remote API (batch as JSON)
    results_json = [r.to_dict() for r in all_results]
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, cookies=cookies) as client:
        resp = await client.post(
            f"{remote.rstrip('/')}/api/results/batch",
            json=results_json,
        )
        if resp.status_code == 200:
            print(f"\nPushed {len(results_json)} results to {remote}")
        else:
            print(f"\nFailed to push results: HTTP {resp.status_code} {resp.text[:200]}")


async def main() -> None:
    from app.config import get_settings
    settings = get_settings()

    p = argparse.ArgumentParser(description="Local GoWild scanner (run from home, not Railway)")
    p.add_argument("--origin", default=settings.HOME_AIRPORT, help="Origin airport code")
    p.add_argument("--dests", nargs="+", help="Destination codes (default: all enabled routes)")
    p.add_argument("--days", type=int, default=2, help="Days ahead to scan (default: 2 — GoWild books 1-2 days out)")
    p.add_argument("--remote", help="Remote GoWild Radar URL to push results to")
    p.add_argument("--password", help="Admin password for remote instance")
    args = p.parse_args()

    # Default destinations from seed routes if not specified
    if not args.dests:
        seed_path = Path(__file__).parent.parent / "data" / "seed_routes.csv"
        if seed_path.exists():
            lines = seed_path.read_text().strip().splitlines()
            dests = []
            for line in lines:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[0].upper() == args.origin.upper():
                    dests.append(parts[1].upper())
            if not dests:
                print(f"No routes found for {args.origin} in seed_routes.csv")
                print("Use --dests ATL DEN LAS MCO to specify destinations")
                return
            args.dests = dests
        else:
            print("No --dests specified and seed_routes.csv not found")
            return

    print(f"GoWild Local Scanner")
    print(f"Origin: {args.origin}")
    print(f"Destinations: {', '.join(args.dests)}")
    print(f"Days ahead: {args.days}")
    print(f"Mock mode: {settings.MOCK_MODE}")
    if args.remote:
        print(f"Remote: {args.remote}")

    if args.remote:
        if not args.password:
            import getpass
            args.password = getpass.getpass("Remote admin password: ")
        await scan_and_post(args.origin, args.dests, args.days, args.remote, args.password)
    else:
        await scan_locally(args.origin, args.dests, args.days)


if __name__ == "__main__":
    asyncio.run(main())
