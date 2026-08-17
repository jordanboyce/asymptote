#!/usr/bin/env python
"""Create a demo client so a fresh install is not an empty screen.

Why this exists: on first launch Finn shows nothing, so a tester's or a
prospect's first impression depends entirely on their own export landing well
on the first try. That is the thing the demo is supposed to *prove*, not the
thing it should *depend on*. This seeds one household with holdings and an IPS,
so the app has something to show before anyone uploads anything.

It drives the HTTP API rather than writing to the store directly, so the seed
file goes through the same ingest path a real advisor's file takes -- vendor
profile match, header detection, numeric coercion, Trust Report. If ingest
breaks, seeding breaks, which is the correct coupling.

    python scripts/seed_demo_client.py                       # against localhost
    python scripts/seed_demo_client.py --base-url https://finn.example.com
    python scripts/seed_demo_client.py --force               # replace existing

Idempotent: a second run finds the collection and stops, so it is safe in a
container start script or a post-deploy step.

The data is fictional and is labelled as such in the collection name. It is
built to exercise the prep cascade end to end -- there is a prohibited holding,
a position over the concentration ceiling, three harvestable losses, and enough
allocation drift to move two asset classes outside their bands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_CSV = REPO_ROOT / "seed" / "demo_client_holdings.csv"

COLLECTION_NAME = "Demo — Alvarez Family Trust (sample data)"
COLLECTION_DESCRIPTION = (
    "Fictional household used to demonstrate the meeting-prep loop. "
    "Safe to delete."
)

# The IPS the holdings are designed to violate in specific, explainable ways.
# Every breach below is arithmetic the advisor can check by hand against the
# CSV, which is the point -- prep must never assert something unverifiable.
DEMO_PROFILE = {
    "display_name": "Alvarez Family Trust",
    "household_members": [
        {"name": "Elena Alvarez", "relationship": "primary", "birth_year": 1965,
         "retirement_year": 2030},
        {"name": "Marco Alvarez", "relationship": "spouse", "birth_year": 1963,
         "retirement_year": 2028},
    ],
    "risk_tolerance": "moderate",
    "risk_notes": (
        "Willingness is moderate; capacity is higher. Elena talked down from a "
        "growth sleeve after the 2022 drawdown -- revisit only if she raises it."
    ),
    "time_horizon_years": 9,
    "goals": [
        {
            "label": "Retire at 65 with $180k/yr pre-tax",
            "target_amount": 4200000,
            "target_date": "2030-06-30",
            "priority": "high",
        },
        {
            "label": "Fund grandchildren's 529s",
            "target_amount": 240000,
            "target_date": "2032-09-01",
            "priority": "medium",
        },
    ],
    "ips": {
        "allocation_targets": [
            {"asset_class": "Common Stocks", "target_pct": 45},
            {"asset_class": "Exchange Traded Funds", "target_pct": 20},
            {"asset_class": "Corporate Bonds", "target_pct": 25},
            {"asset_class": "Money Market Funds", "target_pct": 10},
        ],
        "rebalance_band_pct": 5.0,
        # TXN is 21.85% of the household -- comfortably through this ceiling.
        "max_single_position_pct": 15.0,
        "min_cash_pct": 5.0,
        "max_cash_pct": 15.0,
        # The position is in the file. This is the item prep must sort to the
        # top, ahead of wash-sale risk and harvestable losses.
        "prohibited_holdings": ["XOM"],
        "notes": (
            "Energy majors excluded at the client's request (2024 review). "
            "Legacy TXN lot from Marco's employment is held for tax reasons; "
            "the concentration breach is known and revisited every review."
        ),
    },
    "tax": {
        "filing_status": "married_joint",
        "federal_bracket_pct": 32.0,
        "state": "MA",
        "state_bracket_pct": 5.0,
        "capital_loss_carryforward": 18000.0,
        "ytd_realized_gains": 41500.0,
    },
    "liquidity": {
        "cash_reserve_target": 240000.0,
        "annual_withdrawal": 96000.0,
        "next_liquidity_event": "Q2 2027 roof replacement",
        "next_liquidity_amount": 85000.0,
    },
    "review_frequency": "quarterly",
    "notes": (
        "Prefers one page and the arithmetic behind any recommendation. "
        "Do not lead with performance -- lead with what changed since last time."
    ),
}


class SeedError(RuntimeError):
    pass


def _request(method: str, url: str, *, body=None, headers=None, timeout=120):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SeedError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SeedError(
            f"{method} {url} -> cannot reach the server ({exc.reason}). "
            f"Is Finn running, and is --base-url right?"
        ) from exc


def _upload(base_url: str, collection_id: str, path: Path, timeout: int):
    """Multipart upload without pulling in requests -- stdlib only."""
    boundary = "----finn-seed-boundary-8a31f0"
    payload = bytearray()
    payload += f"--{boundary}\r\n".encode()
    payload += (
        f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode()
    payload += path.read_bytes()
    payload += f"\r\n--{boundary}--\r\n".encode()

    url = f"{base_url}/documents/upload?collection_id={collection_id}"
    req = urllib.request.Request(
        url,
        data=bytes(payload),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SeedError(f"upload -> HTTP {exc.code}: {detail}") from exc


def find_existing(base_url: str):
    collections = _request("GET", f"{base_url}/api/collections") or []
    if isinstance(collections, dict):
        collections = collections.get("collections", [])
    for coll in collections:
        if coll.get("name") == COLLECTION_NAME:
            return coll
    return None


def seed(base_url: str, force: bool, timeout: int) -> int:
    if not SEED_CSV.exists():
        raise SeedError(f"seed file missing: {SEED_CSV}")

    existing = find_existing(base_url)
    if existing and not force:
        print(f"Demo client already present (collection {existing['id']}). Nothing to do.")
        print("Re-run with --force to delete and recreate it.")
        return 0

    if existing and force:
        print(f"Removing existing demo collection {existing['id']}...")
        _request("DELETE", f"{base_url}/api/collections/{existing['id']}")

    print(f"Creating collection {COLLECTION_NAME!r}...")
    collection = _request(
        "POST",
        f"{base_url}/api/collections",
        body={
            "name": COLLECTION_NAME,
            "description": COLLECTION_DESCRIPTION,
            "color": "#8b5cf6",
        },
    )
    collection_id = collection["id"]

    print(f"Uploading {SEED_CSV.name} through the normal ingest path...")
    result = _upload(base_url, collection_id, SEED_CSV, timeout)
    if result:
        # Shape varies by version; surface whatever the server chose to say.
        summary = result.get("message") or result.get("status") or "uploaded"
        print(f"  ingest: {summary}")

    print("Saving the client profile / IPS...")
    _request(
        "PUT",
        f"{base_url}/api/collections/{collection_id}/profile",
        body=DEMO_PROFILE,
    )

    print()
    print(f"Done. Collection id: {collection_id}")
    print("Open the app and run 'Prep for meeting' on it -- prep should surface,")
    print("in priority order: the prohibited XOM position, the TXN concentration")
    print("breach, cash overweight, and three harvestable losses.")
    return 0


def default_base_url() -> str:
    """Match the port the app actually binds.

    PORT in .env is authoritative on a given box; the project default is 8000.
    Getting this wrong sends the operator hunting for a server that is running
    fine on another port.
    """
    port = os.environ.get("PORT")
    if not port:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("PORT=") and not line.startswith("#"):
                    port = line.split("=", 1)[1].strip()
                    break
    return f"http://localhost:{port or '8000'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base-url",
        default=default_base_url(),
        help="Base URL of a running Finn instance (default: reads PORT from .env).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and recreate the demo client if it already exists.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to allow for the upload/ingest call (default: 300).",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    print(f"Seeding demo client against {base_url}")
    try:
        return seed(base_url, args.force, args.timeout)
    except SeedError as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
