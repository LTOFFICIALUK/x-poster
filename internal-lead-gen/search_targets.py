"""
Target list for Places Text Search ingestion.

Each run shuffles this list (when using --max-new) so coverage rotates across markets.
`max_pages` defaults to 1 per target for incremental scheduled pulls.
"""

from __future__ import annotations

from typing import TypedDict


class SearchTarget(TypedDict, total=False):
    keyword: str
    location: str
    region_code: str
    included_type: str
    max_pages: int
    latitude: float
    longitude: float
    radius_m: float


def _cross(
    keywords: list[str],
    locations: list[str],
    region_code: str,
    *,
    included_by_keyword: dict[str, str] | None = None,
    max_pages: int = 1,
) -> list[SearchTarget]:
    out: list[SearchTarget] = []
    inc = included_by_keyword or {}
    for kw in keywords:
        for loc in locations:
            t: SearchTarget = {
                "keyword": kw,
                "location": loc,
                "region_code": region_code,
                "max_pages": max_pages,
            }
            if kw in inc:
                t["included_type"] = inc[kw]
            out.append(t)
    return out


_KEYWORDS_PRIMARY = [
    "gym",
    "fitness studio",
    "yoga studio",
    "pilates studio",
    "CrossFit",
    "personal trainer",
    "wellness centre",
    "martial arts gym",
]

_KEYWORDS_SECONDARY = [
    "barre fitness",
    "spin studio",
    "climbing gym",
    "boutique fitness",
]

_LOCATIONS_UK = [
    "London UK",
    "Manchester UK",
    "Birmingham UK",
    "Leeds UK",
    "Glasgow UK",
    "Edinburgh UK",
    "Liverpool UK",
    "Bristol UK",
    "Cardiff UK",
    "Sheffield UK",
    "Newcastle UK",
]

_LOCATIONS_US = [
    "New York NY USA",
    "Los Angeles CA USA",
    "Chicago IL USA",
    "Houston TX USA",
    "Phoenix AZ USA",
    "Philadelphia PA USA",
    "San Antonio TX USA",
    "San Diego CA USA",
    "Dallas TX USA",
    "Austin TX USA",
    "San Jose CA USA",
    "Miami FL USA",
]

_LOCATIONS_CA = [
    "Toronto ON Canada",
    "Montreal QC Canada",
    "Vancouver BC Canada",
    "Calgary AB Canada",
    "Edmonton AB Canada",
    "Ottawa ON Canada",
    "Mississauga ON Canada",
    "Winnipeg MB Canada",
]

_LOCATIONS_AU = [
    "Sydney NSW Australia",
    "Melbourne VIC Australia",
    "Brisbane QLD Australia",
    "Perth WA Australia",
    "Adelaide SA Australia",
    "Gold Coast QLD Australia",
    "Canberra ACT Australia",
    "Newcastle NSW Australia",
]

_INCLUDED_GYM = {"gym": "gym"}

_HUB_LOCATIONS: list[tuple[str, str]] = [
    ("London UK", "GB"),
    ("Manchester UK", "GB"),
    ("Los Angeles CA USA", "US"),
    ("Chicago IL USA", "US"),
    ("Toronto ON Canada", "CA"),
    ("Vancouver BC Canada", "CA"),
    ("Sydney NSW Australia", "AU"),
    ("Melbourne VIC Australia", "AU"),
]


def _hub_secondary_kw() -> list[SearchTarget]:
    out: list[SearchTarget] = []
    for kw in _KEYWORDS_SECONDARY:
        for location, rc in _HUB_LOCATIONS:
            t: SearchTarget = {
                "keyword": kw,
                "location": location,
                "region_code": rc,
                "max_pages": 1,
            }
            out.append(t)
    return out


SEARCH_TARGETS: list[SearchTarget] = (
    _cross(_KEYWORDS_PRIMARY, _LOCATIONS_UK, "GB", included_by_keyword=_INCLUDED_GYM)
    + _cross(_KEYWORDS_PRIMARY, _LOCATIONS_US, "US", included_by_keyword=_INCLUDED_GYM)
    + _cross(_KEYWORDS_PRIMARY, _LOCATIONS_CA, "CA", included_by_keyword=_INCLUDED_GYM)
    + _cross(_KEYWORDS_PRIMARY, _LOCATIONS_AU, "AU")
    + _hub_secondary_kw()
)
