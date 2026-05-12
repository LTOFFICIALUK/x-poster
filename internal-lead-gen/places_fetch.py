"""
Google Places API (New) — text search for lead discovery.

Uses the official Text Search endpoint (not HTML scraping): compliant and stable
for server-side ingestion. Enable "Places API (New)" on the GCP key.

Run from repo root (`teventis-automations`):

  pip install -r requirements.txt
  export GOOGLE_PLACES_API_KEY=...
  python internal-lead-gen/places_fetch.py --query "gym" --location "Leeds UK" \\
      --region-code GB --included-type gym
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# Load .env from automation repo root regardless of cwd
_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Cost/latency: request only fields we need — add more paths if product requires them.
_FIELD_MASK_BASE = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.types",
        "nextPageToken",
    ]
)


def places_text_search(
    api_key: str,
    text_query: str,
    *,
    region_code: str | None = None,
    language_code: str = "en",
    included_type: str | None = None,
    max_result_count: int = 20,
    page_token: str | None = None,
    location_bias_latitude: float | None = None,
    location_bias_longitude: float | None = None,
    location_bias_radius_m: float | None = None,
    extra_field_mask_suffix: str | None = None,
) -> dict[str, Any]:
    """
    One POST to `places:searchText`. Pagination: pass `nextPageToken` from the
    previous response body as `page_token` until absent.
    """
    body: dict[str, Any] = {
        "textQuery": text_query,
        "languageCode": language_code,
        "maxResultCount": max(1, min(int(max_result_count), 20)),
    }
    if region_code:
        body["regionCode"] = region_code.upper()
    if included_type:
        body["includedType"] = included_type
    if page_token:
        body["pageToken"] = page_token
    if location_bias_latitude is not None and location_bias_longitude is not None:
        circle_radius = (
            location_bias_radius_m if location_bias_radius_m is not None else 50_000.0
        )
        body["locationBias"] = {
            "circle": {
                "center": {
                    "latitude": location_bias_latitude,
                    "longitude": location_bias_longitude,
                },
                "radius": circle_radius,
            }
        }

    field_mask = _FIELD_MASK_BASE
    if extra_field_mask_suffix:
        field_mask = f"{field_mask},{extra_field_mask_suffix}"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }

    response = httpx.post(SEARCH_URL, headers=headers, json=body, timeout=60.0)
    response.raise_for_status()
    return response.json()


def flatten_place(place: dict[str, Any]) -> dict[str, Any]:
    """Normalised dict for CSV / CRM / Postgres `metadata` payloads."""
    display = place.get("displayName") or {}
    title = display.get("text") if isinstance(display, dict) else None
    return {
        "google_place_id": place.get("id"),
        "business_name": title,
        "location": place.get("formattedAddress"),
        "phone": place.get("nationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "types": place.get("types"),
        # Phones are mandatory downstream — callers should filter blanks.
        "phone_present": bool(place.get("nationalPhoneNumber")),
    }


def collect_pages(
    api_key: str,
    text_query: str,
    *,
    region_code: str | None,
    included_type: str | None,
    max_pages: int,
    location_bias_latitude: float | None = None,
    location_bias_longitude: float | None = None,
    location_bias_radius_m: float | None = None,
) -> list[dict[str, Any]]:
    """Follow nextPageToken until exhausted or max_pages reached."""
    flat: list[dict[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    total_pages = 0

    while total_pages < max_pages:
        raw = places_text_search(
            api_key,
            text_query,
            region_code=region_code,
            included_type=included_type,
            page_token=page_token,
            location_bias_latitude=location_bias_latitude,
            location_bias_longitude=location_bias_longitude,
            location_bias_radius_m=location_bias_radius_m,
        )
        for p in raw.get("places") or []:
            flat.append(flatten_place(p))

        total_pages += 1
        next_tok = raw.get("nextPageToken")
        if not next_tok or next_tok in seen_tokens:
            break
        seen_tokens.add(next_tok)
        page_token = next_tok

    return flat


def main() -> None:
    parser = argparse.ArgumentParser(description="Places API (New) text search CLI")
    parser.add_argument("--query", required=True, help="e.g. gym, yoga studio, personal trainer")
    parser.add_argument(
        "--location",
        default="",
        help='Appended into text query, e.g. "Manchester UK"',
    )
    parser.add_argument(
        "--region-code",
        default=None,
        help="ISO 3166-1 alpha-2 bias (GB, US, CA, AU, …)",
    )
    parser.add_argument(
        "--included-type",
        default=None,
        help='Single Places type filter, e.g. "gym" (optional)',
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Max result pages per query (caps API spend)",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=None,
        help="Centre latitude for circular locationBias",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        default=None,
        help="Centre longitude for circular locationBias",
    )
    parser.add_argument(
        "--radius-m",
        type=float,
        default=None,
        help="Circle radius in metres for locationBias (default 50 km)",
    )
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print(
            "Set GOOGLE_PLACES_API_KEY (or GOOGLE_MAPS_API_KEY) in .env or the environment.",
            file=sys.stderr,
        )
        sys.exit(1)

    text_query = args.query.strip()
    if args.location.strip():
        text_query = f"{text_query} {args.location.strip()}"

    rows = collect_pages(
        api_key,
        text_query,
        region_code=args.region_code,
        included_type=args.included_type,
        max_pages=max(1, args.pages),
        location_bias_latitude=args.latitude,
        location_bias_longitude=args.longitude,
        location_bias_radius_m=args.radius_m,
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
