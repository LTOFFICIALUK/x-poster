"""
vapi_client.py
==============
Wrapper around Vapi's REST API for the operations we need:

  - create / update an assistant (system prompt, voice, function tools)
  - link a phone number to an assistant
  - send SMS (used for booking + callback confirmations)

Vapi's runtime side is the one calling US (webhooks). This module is for the
infrequent admin actions: deploying assistant config, sending an SMS, looking
something up.

>>> NOTE FOR LUKE <<<
  These endpoint URLs and payloads are written from memory of Vapi's API as of
  early 2026. Verify against https://docs.vapi.ai/api-reference once you've
  signed up. Marked TODOs flag the riskiest assumptions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import httpx
except ImportError as e:
    raise SystemExit("httpx not installed. Run: pip install httpx") from e


VAPI_API_BASE = "https://api.vapi.ai"


class VapiError(RuntimeError):
    """Any non-2xx response from Vapi bubbles up as this."""


@dataclass(frozen=True)
class VapiAssistant:
    id: str
    name: str


class VapiClient:
    def __init__(self, api_key: str | None = None, *, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.getenv("VAPI_API_KEY")
        if not self.api_key:
            raise SystemExit(
                "VAPI_API_KEY not set. Get it from your Vapi dashboard, then "
                "add VAPI_API_KEY=... to .env."
            )
        self._client = httpx.Client(
            base_url=VAPI_API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    # ── Assistant CRUD ──────────────────────────────────────────────────────

    def create_assistant(self, payload: dict[str, Any]) -> VapiAssistant:
        """POST /assistant — create a new assistant. Returns its id + name."""
        r = self._client.post("/assistant", json=payload)
        if r.status_code not in (200, 201):
            raise VapiError(f"POST /assistant failed: {r.status_code} {r.text}")
        body = r.json()
        return VapiAssistant(id=body["id"], name=body.get("name", payload.get("name", "")))

    def update_assistant(self, assistant_id: str, payload: dict[str, Any]) -> VapiAssistant:
        """PATCH /assistant/{id} — update existing assistant config."""
        r = self._client.patch(f"/assistant/{assistant_id}", json=payload)
        if r.status_code not in (200, 201):
            raise VapiError(f"PATCH /assistant/{assistant_id} failed: {r.status_code} {r.text}")
        body = r.json()
        return VapiAssistant(id=body["id"], name=body.get("name", payload.get("name", "")))

    def get_assistant(self, assistant_id: str) -> dict[str, Any]:
        r = self._client.get(f"/assistant/{assistant_id}")
        if r.status_code != 200:
            raise VapiError(f"GET /assistant/{assistant_id} failed: {r.status_code} {r.text}")
        return r.json()

    # ── Phone numbers ───────────────────────────────────────────────────────

    def list_phone_numbers(self) -> list[dict[str, Any]]:
        r = self._client.get("/phone-number")
        if r.status_code != 200:
            raise VapiError(f"GET /phone-number failed: {r.status_code} {r.text}")
        return r.json()

    def attach_assistant_to_number(self, phone_number_id: str, assistant_id: str) -> None:
        """
        Link an existing Vapi-provisioned number to an assistant.

        TODO: verify this is a PATCH on /phone-number/{id} with {"assistantId": ...}
        — Vapi has occasionally renamed this field to `assistant.id`.
        """
        r = self._client.patch(
            f"/phone-number/{phone_number_id}",
            json={"assistantId": assistant_id},
        )
        if r.status_code not in (200, 201):
            raise VapiError(
                f"PATCH /phone-number/{phone_number_id} failed: {r.status_code} {r.text}"
            )

    # ── SMS ─────────────────────────────────────────────────────────────────

    def send_sms(self, *, from_phone_number_id: str, to_e164: str, body: str) -> dict[str, Any]:
        """
        Send an outbound SMS via the linked phone number.

        TODO: verify Vapi's exact SMS endpoint. As of writing it is:
          POST /sms
          { "phoneNumberId": <vapi phone number id>,
            "customer": {"number": <E.164>},
            "message": <text> }
        """
        r = self._client.post(
            "/sms",
            json={
                "phoneNumberId": from_phone_number_id,
                "customer": {"number": to_e164},
                "message": body,
            },
        )
        if r.status_code not in (200, 201):
            raise VapiError(f"POST /sms failed: {r.status_code} {r.text}")
        return r.json()
