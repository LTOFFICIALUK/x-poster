"""
calendly_client.py
==================
Thin wrapper around Calendly's REST API for the operations Riley needs:

  - resolve the current user + event-type URIs
  - list available time slots for a date range
  - create a single-use scheduling link the lead clicks to actually book

Calendly does not currently support fully automated programmatic booking — the
lead has to confirm via Calendly's hosted page. The pattern we use:

  1. Riley confirms a time verbally with the lead.
  2. We create a single-use scheduling link (max_event_count=1) for the
     configured event type.
  3. We SMS that link to the lead's mobile.
  4. The lead taps the link, fills in the standard form, and Calendly fires its
     own webhook to confirm the booking.

If/when Calendly ships a true booking endpoint, swap `create_single_use_link`
for the direct-book call without changing the rest of the surface.

>>> NOTE FOR LUKE <<<
  These endpoint URLs and payloads are written from memory of Calendly's v2
  API. Verify against https://developer.calendly.com/api-docs once you have
  the PAT. Marked TODOs in the code are the spots most likely to need a tweak.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ImportError as e:
    raise SystemExit("httpx not installed. Run: pip install httpx") from e


CALENDLY_API_BASE = "https://api.calendly.com"


# ─── Errors ──────────────────────────────────────────────────────────────────

class CalendlyError(RuntimeError):
    """Any non-2xx response from Calendly bubbles up as this."""


# ─── Slot dataclass ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Slot:
    start_iso: str        # e.g. "2026-05-12T14:00:00.000000Z"
    scheduling_url: str   # Calendly's per-slot scheduling URL


# ─── Client ──────────────────────────────────────────────────────────────────

class CalendlyClient:
    """Stateless-ish wrapper. Construct once per request or per app process."""

    def __init__(self, pat: str | None = None, *, timeout: float = 15.0) -> None:
        self.pat = pat or os.getenv("CALENDLY_PAT")
        if not self.pat:
            raise SystemExit(
                "CALENDLY_PAT not set. Get a Personal Access Token from your "
                "Calendly account → Integrations → API & Webhooks, then add "
                "CALENDLY_PAT=... to .env."
            )
        self._client = httpx.Client(
            base_url=CALENDLY_API_BASE,
            headers={
                "Authorization": f"Bearer {self.pat}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    # ── Identity ────────────────────────────────────────────────────────────

    def get_current_user_uri(self) -> str:
        """GET /users/me → resource.uri. Cached by the caller."""
        r = self._client.get("/users/me")
        if r.status_code != 200:
            raise CalendlyError(f"GET /users/me failed: {r.status_code} {r.text}")
        return r.json()["resource"]["uri"]

    # ── Event types ─────────────────────────────────────────────────────────

    def find_event_type_uri_by_url(self, scheduling_url: str, *, user_uri: str | None = None) -> str:
        """
        Given a public scheduling URL like 'https://calendly.com/rio-teventis/30min',
        return the matching event-type URI.

        TODO: verify Calendly actually returns `scheduling_url` in the event_types
        list; if not, match by `slug` parsed off the public URL.
        """
        if user_uri is None:
            user_uri = self.get_current_user_uri()

        r = self._client.get("/event_types", params={"user": user_uri, "active": "true"})
        if r.status_code != 200:
            raise CalendlyError(f"GET /event_types failed: {r.status_code} {r.text}")
        items = r.json().get("collection", [])
        for et in items:
            if et.get("scheduling_url") == scheduling_url.rstrip("/"):
                return et["uri"]

        # Fall back to slug-match if the field isn't present.
        slug = scheduling_url.rstrip("/").rsplit("/", 1)[-1]
        for et in items:
            if et.get("slug") == slug:
                return et["uri"]

        raise CalendlyError(
            f"No event type matched scheduling_url={scheduling_url!r}. "
            f"Found {[(et.get('name'), et.get('scheduling_url')) for et in items]!r}"
        )

    # ── Availability ────────────────────────────────────────────────────────

    def list_available_times(
        self,
        event_type_uri: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 10,
    ) -> list[Slot]:
        """
        List bookable times for the given event type between `start` and `end`.
        Defaults to "next 7 days starting now" — wide enough for Riley to offer
        a couple of options, narrow enough to stay within Calendly's range cap
        (which is 7 days per call on /event_type_available_times).

        TODO: verify the parameter shape. As of writing the endpoint accepts
        `event_type`, `start_time`, `end_time` as ISO strings (UTC).
        """
        if start is None:
            start = datetime.now(timezone.utc)
        if end is None:
            end = start + timedelta(days=7)

        r = self._client.get(
            "/event_type_available_times",
            params={
                "event_type": event_type_uri,
                "start_time": _iso(start),
                "end_time":   _iso(end),
            },
        )
        if r.status_code != 200:
            raise CalendlyError(
                f"GET /event_type_available_times failed: {r.status_code} {r.text}"
            )
        items = r.json().get("collection", [])[:max_results]
        return [
            Slot(start_iso=item["start_time"], scheduling_url=item["scheduling_url"])
            for item in items
        ]

    # ── Booking (single-use link) ───────────────────────────────────────────

    def create_single_use_link(self, event_type_uri: str) -> str:
        """
        Create a single-use scheduling link. The lead taps this and books
        themselves on Calendly's hosted page.

        TODO: verify body shape. The endpoint historically expects:
          POST /scheduling_links
          { "max_event_count": 1, "owner": <event_type_uri>, "owner_type": "EventType" }
        """
        r = self._client.post(
            "/scheduling_links",
            json={
                "max_event_count": 1,
                "owner": event_type_uri,
                "owner_type": "EventType",
            },
        )
        if r.status_code not in (200, 201):
            raise CalendlyError(
                f"POST /scheduling_links failed: {r.status_code} {r.text}"
            )
        body = r.json().get("resource") or r.json()
        url = body.get("booking_url")
        if not url:
            raise CalendlyError(f"No booking_url in /scheduling_links response: {r.text}")
        return url

    # ── Optional: resolve a booked event after Calendly fires its webhook ──

    def get_event(self, event_uri: str) -> dict[str, Any]:
        """Look up a scheduled event by its URI. Used by our webhook listener
        to verify a booking after Calendly notifies us. Not used in v1 hot path."""
        r = self._client.get(event_uri.replace(CALENDLY_API_BASE, "") or event_uri)
        if r.status_code != 200:
            raise CalendlyError(f"GET event failed: {r.status_code} {r.text}")
        return r.json().get("resource") or r.json()


def _iso(dt: datetime) -> str:
    """Return Calendly-friendly ISO with explicit UTC offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
