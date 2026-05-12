"""
Ingest Google Places results into `leads` (source = google_maps).

Requires:
  - DATABASE_URL
  - GOOGLE_PLACES_API_KEY or GOOGLE_MAPS_API_KEY

Run from `teventis-automations` root:

  python internal-lead-gen/ingest_maps.py --dry-run
  python internal-lead-gen/ingest_maps.py --max-new 5
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    import psycopg
except ImportError as e:
    raise SystemExit("Install psycopg: pip install 'psycopg[binary]'") from e

_ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(_ROOT / ".env")

# Package folder name has a hyphen — load sibling module by path.
_places_path = Path(__file__).resolve().parent / "places_fetch.py"
_spec = importlib.util.spec_from_file_location("_ig_places_fetch", _places_path)
if _spec is None or _spec.loader is None:
    raise SystemExit("Could not load places_fetch.py")
_places = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_places)
collect_pages = _places.collect_pages

from search_targets import SEARCH_TARGETS, SearchTarget

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# When --max-new is set, stop after this many full list passes without enough new leads.
_MAX_SHUFFLE_ROUNDS = 6


def _build_metadata(row: dict[str, Any], target: SearchTarget, text_query: str) -> dict[str, Any]:
    return {
        "maps_rating": row.get("rating"),
        "maps_review_count": row.get("review_count"),
        "maps_types": row.get("types"),
        "ingest": {
            "keyword": target.get("keyword"),
            "location": target.get("location"),
            "text_query": text_query,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    }


def upsert_lead(conn: psycopg.Connection, row: dict[str, Any], meta: dict[str, Any]) -> str:
    """Returns 'new', 'updated', or 'skip'."""
    place_id = row.get("google_place_id")
    phone = (row.get("phone") or "").strip()
    if not place_id or not phone:
        return "skip"

    business = (row.get("business_name") or "").strip() or None
    location = (row.get("location") or "").strip() or None
    website = (row.get("website") or "").strip() or None
    meta_json = json.dumps(meta)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM leads WHERE google_place_id = %s LIMIT 1",
            (place_id,),
        )
        existed = cur.fetchone() is not None

        cur.execute(
            """
            INSERT INTO leads (
              business_name, phone, location, website,
              source, temperature, status, metadata, google_place_id
            )
            VALUES (%s, %s, %s, %s, 'google_maps', 'cold', 'new', %s::jsonb, %s)
            ON CONFLICT (google_place_id) DO UPDATE SET
              business_name = COALESCE(EXCLUDED.business_name, leads.business_name),
              phone = COALESCE(EXCLUDED.phone, leads.phone),
              location = COALESCE(EXCLUDED.location, leads.location),
              website = COALESCE(EXCLUDED.website, leads.website),
              metadata = COALESCE(leads.metadata, '{}'::jsonb)
                || COALESCE(EXCLUDED.metadata, '{}'::jsonb),
              updated_at = NOW()
            """,
            (business, phone, location, website, meta_json, place_id),
        )
    return "updated" if existed else "new"


def run_ingest(
    *,
    dry_run: bool,
    only_index: int | None,
    max_new: int | None,
    api_key: str,
    database_url: str,
) -> None:
    targets = list(SEARCH_TARGETS)
    if only_index is not None:
        if only_index < 0 or only_index >= len(targets):
            raise SystemExit(f"--only-index must be 0..{len(targets) - 1}")
        targets = [targets[only_index]]

    stats: dict[str, int] = {
        "skip": 0,
        "new": 0,
        "updated": 0,
        "rows_seen": 0,
        "would_upsert": 0,
    }

    if dry_run:
        conn = None
    else:
        conn = psycopg.connect(database_url)

    def process_row(target: SearchTarget, text_query: str, row: dict[str, Any]) -> bool:
        """Returns True when caller should stop the whole ingest (cap reached)."""
        meta = _build_metadata(row, target, text_query)
        if dry_run:
            if not row.get("google_place_id") or not row.get("phone"):
                stats["skip"] += 1
                return False
            if max_new is not None:
                stats["new"] += 1
                return stats["new"] >= max_new
            stats["would_upsert"] += 1
            return False

        assert conn is not None
        action = upsert_lead(conn, row, meta)
        if action == "skip":
            stats["skip"] += 1
        elif action == "new":
            stats["new"] += 1
            conn.commit()
            return max_new is not None and stats["new"] >= max_new
        else:
            stats["updated"] += 1
            conn.commit()
        return False

    def fetch_and_process_target(target: SearchTarget) -> bool:
        kw = target.get("keyword", "").strip()
        loc = target.get("location", "").strip()
        text_query = f"{kw} {loc}".strip()
        if not text_query:
            log.warning("Skipping empty target: %s", target)
            return False

        region = target.get("region_code")
        included = target.get("included_type")
        max_pages = int(target.get("max_pages", 1))
        lat = target.get("latitude")
        lon = target.get("longitude")
        rad = target.get("radius_m")

        log.info("Fetching: %r (region=%s pages=%s)", text_query, region, max_pages)
        rows = collect_pages(
            api_key,
            text_query,
            region_code=region,
            included_type=included,
            max_pages=max_pages,
            location_bias_latitude=lat,
            location_bias_longitude=lon,
            location_bias_radius_m=rad,
        )
        stats["rows_seen"] += len(rows)

        for row in rows:
            stop = process_row(target, text_query, row)
            if stop:
                return True
        return False

    try:
        if only_index is not None:
            for target in targets:
                if fetch_and_process_target(target):
                    break
        elif max_new is not None and max_new > 0:
            for round_i in range(_MAX_SHUFFLE_ROUNDS):
                random.shuffle(targets)
                if round_i == 0:
                    log.info("Max-new=%s shuffle round %s/%s", max_new, round_i + 1, _MAX_SHUFFLE_ROUNDS)
                else:
                    log.info("Max-new=%s shuffle round %s/%s (new so far=%s)", max_new, round_i + 1, _MAX_SHUFFLE_ROUNDS, stats["new"])

                for target in targets:
                    if stats["new"] >= max_new:
                        break
                    if fetch_and_process_target(target):
                        break
                if stats["new"] >= max_new:
                    break
            if stats["new"] < max_new and not dry_run:
                log.warning(
                    "Only added %s new lead(s); wanted %s (pool may be saturated for current queries)",
                    stats["new"],
                    max_new,
                )
        elif max_new is None:
            random.shuffle(targets)
            for target in targets:
                fetch_and_process_target(target)
    finally:
        if conn is not None:
            conn.close()

    log.info(
        "Done: seen=%s new=%s updated=%s skip=%s would_upsert=%s dry_run=%s",
        stats["rows_seen"],
        stats["new"],
        stats["updated"],
        stats["skip"],
        stats["would_upsert"],
        dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Google Places into leads table")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only; no DB writes")
    parser.add_argument(
        "--only-index",
        type=int,
        default=None,
        help=f"Run only SEARCH_TARGETS[n] (0..{len(SEARCH_TARGETS) - 1})",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N genuinely new rows (skipped if already in DB by google_place_id)",
    )
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        log.error("Set GOOGLE_PLACES_API_KEY or GOOGLE_MAPS_API_KEY in .env")
        sys.exit(1)

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url and not args.dry_run:
        log.error("DATABASE_URL is required unless --dry-run")
        sys.exit(1)

    run_ingest(
        dry_run=args.dry_run,
        only_index=args.only_index,
        max_new=args.max_new,
        api_key=api_key,
        database_url=database_url,
    )


if __name__ == "__main__":
    main()
