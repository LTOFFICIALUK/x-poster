"""
server.py
==========
FastAPI service that handles Vapi webhooks for Riley.

Endpoints:
  POST /vapi/webhook   — Vapi's single webhook for all assistant events.
  GET  /health         — liveness / readiness probe.

Vapi sends one webhook URL all events to. We dispatch on `message.type`:

  - "function-call"       — Riley invoked one of our tools mid-call.
                            We compute the result and return it inline.
  - "end-of-call-report"  — Vapi finalising the call: transcript, recording,
                            summary, ended_reason. We persist everything.
  - "status-update"       — phase changes (e.g. "in-progress" → "ended").
                            Logged for visibility.
  - "transcript"          — live transcript chunks (we ignore in v1; we lean on
                            end-of-call-report for the full transcript).
  - "assistant-request"   — Vapi asking which assistant to use for an inbound
                            call. We look up by phone-number id.

Runs as its own systemd service (`teventis-telephony.service`) on port
$TELEPHONY_PORT (default 8088). The news-automation scheduler keeps running
in `teventis-automations.service` independently.

Local:   uvicorn server:app --reload --port 8088
Prod:    systemctl restart teventis-telephony
"""

from __future__ import annotations

# ── sys.path bootstrap ──────────────────────────────────────────────────────
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# ────────────────────────────────────────────────────────────────────────────

import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
load_dotenv()  # before importing telephony_db (it reads env)

try:
    from fastapi import FastAPI, Request, HTTPException, status
    from fastapi.responses import JSONResponse
except ImportError as e:
    raise SystemExit("fastapi not installed. Run: pip install fastapi uvicorn") from e

import telephony_db
from telephony_db import TelephonyAutomation
from calendly_client import CalendlyClient, CalendlyError, Slot
from vapi_client import VapiClient, VapiError
from riley_prompt import VALID_SERVICE_SLUGS


# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [riley] %(message)s",
)
log = logging.getLogger(__name__)


# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="Teventis Telephony — Riley")

# Optional shared secret — Vapi can be configured to send a bearer/signing key
# with each webhook. Verify against this before trusting the payload. Empty =
# disabled (dev). Set TELEPHONY_WEBHOOK_SECRET in prod and configure Vapi to
# send it.
WEBHOOK_SECRET = os.getenv("TELEPHONY_WEBHOOK_SECRET", "")


# ─── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "teventis-telephony", "now": _now_iso()}


# ─── Webhook ────────────────────────────────────────────────────────────────

@app.post("/vapi/webhook")
async def vapi_webhook(request: Request) -> JSONResponse:
    _verify_secret(request)
    payload = await request.json()
    message = payload.get("message") or {}
    msg_type = message.get("type")
    call = (payload.get("call") or message.get("call") or {})
    vapi_call_id = call.get("id") or message.get("callId")

    log.info("Vapi webhook: type=%s call=%s", msg_type, vapi_call_id)

    if msg_type == "assistant-request":
        return JSONResponse(_handle_assistant_request(payload))

    if msg_type == "function-call":
        return JSONResponse(_handle_function_call(message, call, vapi_call_id))

    if msg_type == "end-of-call-report":
        _handle_end_of_call(message, call, vapi_call_id)
        return JSONResponse({"ok": True})

    if msg_type == "status-update":
        log.info("status-update: %s", message.get("status"))
        return JSONResponse({"ok": True})

    if msg_type in ("transcript", "speech-update", "user-interrupted"):
        # Live events we don't act on yet — no-op so Vapi doesn't retry.
        return JSONResponse({"ok": True})

    log.warning("Unknown Vapi message type: %r — payload keys=%s",
                msg_type, list(payload.keys()))
    return JSONResponse({"ok": True})


# ─── Assistant request — "which assistant for this call?" ───────────────────

def _handle_assistant_request(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Vapi calls this when an inbound call arrives and the phone number isn't
    pre-bound to an assistant. We look up our configured assistant by the
    Vapi phone-number id the call landed on.

    For v1 we keep things simple — there's exactly one telephony automation
    (Rio's). The first active one wins.
    """
    call = payload.get("call") or {}
    phone_number = call.get("phoneNumber") or call.get("phone_number") or {}
    phone_number_id = phone_number.get("id")

    automations = telephony_db.load_active_telephony_automations()
    matched = None
    if phone_number_id:
        for a in automations:
            if a.config.vapi_phone_number_id == phone_number_id:
                matched = a
                break
    if matched is None and automations:
        matched = automations[0]
        log.warning("No phone_number_id match (%s) — falling back to first active automation",
                    phone_number_id)

    if matched is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="No active telephony automation")

    if not matched.config.vapi_assistant_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Automation {matched.id} has no vapi_assistant_id — run vapi_assistant.py upload",
        )

    return {"assistantId": matched.config.vapi_assistant_id}


# ─── Function calls — Riley's tools ─────────────────────────────────────────

def _handle_function_call(
    message: dict[str, Any],
    call: dict[str, Any],
    vapi_call_id: str | None,
) -> dict[str, Any]:
    """
    Riley invoked a tool. We resolve the tool, run it, return the result Vapi
    will pipe back into the conversation.

    Vapi's payload shape for function-call (current):
      message.functionCall = { name, parameters }
    """
    function_call = message.get("functionCall") or {}
    name = function_call.get("name")
    params = function_call.get("parameters") or {}

    automation = _automation_for_call(call)

    try:
        if name == "get_availability":
            return _tool_get_availability(automation, params)
        if name == "book_slot":
            return _tool_book_slot(automation, params, vapi_call_id)
        if name == "log_callback_request":
            return _tool_log_callback_request(automation, params, vapi_call_id)
        if name == "send_sms_now":
            return _tool_send_sms_now(automation, params)
        log.warning("Unknown tool name: %r", name)
        return {"result": {"error": f"unknown_tool:{name}"}}
    except Exception as e:  # never bubble — Riley needs SOMETHING to say
        log.exception("Tool %r failed: %s", name, e)
        return {"result": {"error": str(e)}}


# ── Tool: get_availability ──────────────────────────────────────────────────

def _tool_get_availability(automation: TelephonyAutomation, params: dict[str, Any]) -> dict[str, Any]:
    """Return up to N slots in UK time, ISO-formatted, that Riley can read out."""
    cal = CalendlyClient()
    try:
        event_type_uri = automation.config.calendly_event_type_uri
        if not event_type_uri:
            event_type_uri = cal.find_event_type_uri_by_url(automation.config.calendly_event_url)
            user_uri = cal.get_current_user_uri()
            telephony_db.set_calendly_uris(
                automation_id=automation.id,
                user_uri=user_uri,
                event_type_uri=event_type_uri,
            )
        slots: list[Slot] = cal.list_available_times(event_type_uri, max_results=int(params.get("max_results", 6) or 6))
    finally:
        cal.close()

    return {
        "result": {
            "slots": [{"start_iso": s.start_iso, "scheduling_url": s.scheduling_url} for s in slots],
            "timezone": "Europe/London",
        }
    }


# ── Tool: book_slot ─────────────────────────────────────────────────────────

def _tool_book_slot(
    automation: TelephonyAutomation,
    params: dict[str, Any],
    vapi_call_id: str | None,
) -> dict[str, Any]:
    """
    Create a single-use Calendly link, SMS it to the caller, and record the
    captured fields on the calls row. The actual booking happens when the
    lead taps the link — Calendly's webhook (future work) will flip the row
    from outcome='booked' (optimistic) to a confirmed state.
    """
    start_iso = params.get("start_iso") or params.get("startIso")
    caller_email = params.get("caller_email") or params.get("callerEmail")
    caller_name = params.get("caller_name") or params.get("callerName")
    caller_business = params.get("caller_business") or params.get("callerBusiness")
    caller_region = params.get("caller_region") or params.get("callerRegion")
    service_interest = params.get("service_interest") or params.get("serviceInterest")
    if service_interest and service_interest not in VALID_SERVICE_SLUGS:
        log.warning("Service interest %r not in known set — keeping as-is", service_interest)

    if vapi_call_id:
        telephony_db.update_call_captured_fields(
            vapi_call_id=vapi_call_id,
            caller_name=caller_name,
            caller_business=caller_business,
            caller_email=caller_email,
            caller_region=caller_region,
            service_interest=service_interest,
        )

    cal = CalendlyClient()
    try:
        event_type_uri = automation.config.calendly_event_type_uri or cal.find_event_type_uri_by_url(
            automation.config.calendly_event_url
        )
        booking_url = cal.create_single_use_link(event_type_uri)
    finally:
        cal.close()

    caller_e164 = (
        ((params.get("call") or {}).get("customer") or {}).get("number")
        or _caller_e164_for(vapi_call_id)
    )
    if caller_e164 and automation.config.vapi_phone_number_id:
        try:
            vapi = VapiClient()
            vapi.send_sms(
                from_phone_number_id=automation.config.vapi_phone_number_id,
                to_e164=caller_e164,
                body=(
                    f"Hi{f' {caller_name}' if caller_name else ''}, "
                    f"this is Riley from Teventis. Tap to confirm your call with Rio: "
                    f"{booking_url}\n"
                    f"If anything changes, reply STOP or email Hello@teventis.com."
                ),
            )
            vapi.close()
        except VapiError as e:
            log.error("SMS send failed (continuing): %s", e)

    if vapi_call_id:
        telephony_db.update_call_outcome(
            vapi_call_id=vapi_call_id,
            outcome="booked",
            calendly_event_url=booking_url,
            calendly_scheduled_at_iso=start_iso,
        )

    return {"result": {"ok": True, "booking_url": booking_url}}


# ── Tool: log_callback_request ──────────────────────────────────────────────

def _tool_log_callback_request(
    automation: TelephonyAutomation,
    params: dict[str, Any],
    vapi_call_id: str | None,
) -> dict[str, Any]:
    caller_name = params.get("caller_name") or params.get("callerName")
    caller_email = params.get("caller_email") or params.get("callerEmail")
    reason = params.get("reason") or params.get("reason_summary") or "No reason provided"

    caller_e164 = _caller_e164_for(vapi_call_id) or "unknown"
    cb_id = telephony_db.insert_callback_request(
        automation_id=automation.id,
        client_id=automation.client_id,
        caller_e164=caller_e164,
        call_id=_call_pk_for(vapi_call_id),
        caller_name=caller_name,
        caller_email=caller_email,
        reason_summary=reason,
    )

    # Notify Rio.
    if automation.config.escalation_phone_e164 and automation.config.vapi_phone_number_id:
        try:
            vapi = VapiClient()
            vapi.send_sms(
                from_phone_number_id=automation.config.vapi_phone_number_id,
                to_e164=automation.config.escalation_phone_e164,
                body=(
                    f"Callback request — "
                    f"{(caller_name or 'caller')} ({caller_e164}) — re: {reason}. "
                    f"{f'Email: {caller_email}. ' if caller_email else ''}"
                    f"Logged at {_now_iso()}."
                ),
            )
            vapi.close()
        except VapiError as e:
            log.error("Escalation SMS to Rio failed: %s", e)

    if vapi_call_id:
        telephony_db.update_call_outcome(
            vapi_call_id=vapi_call_id,
            outcome="callback_requested",
            callback_request_id=cb_id,
        )

    return {"result": {"ok": True, "callback_request_id": cb_id}}


# ── Tool: send_sms_now ──────────────────────────────────────────────────────

def _tool_send_sms_now(automation: TelephonyAutomation, params: dict[str, Any]) -> dict[str, Any]:
    """Generic SMS-send tool — rarely needed mid-call but kept available."""
    body = params.get("body") or params.get("message")
    to = params.get("to") or params.get("to_e164")
    if not (body and to):
        return {"result": {"error": "missing 'body' or 'to'"}}
    if not automation.config.vapi_phone_number_id:
        return {"result": {"error": "no vapi_phone_number_id configured"}}
    vapi = VapiClient()
    try:
        vapi.send_sms(
            from_phone_number_id=automation.config.vapi_phone_number_id,
            to_e164=to,
            body=body,
        )
    finally:
        vapi.close()
    return {"result": {"ok": True}}


# ─── End of call ─────────────────────────────────────────────────────────────

def _handle_end_of_call(
    message: dict[str, Any],
    call: dict[str, Any],
    vapi_call_id: str | None,
) -> None:
    """Persist transcript, recording, summary, status."""
    if not vapi_call_id:
        log.error("end-of-call-report with no call id — dropping")
        return

    automation = _automation_for_call(call)

    # Make sure the call row exists (idempotent — call.started should have created it).
    customer = call.get("customer") or {}
    callee = (call.get("phoneNumber") or call.get("phone_number") or {})
    call_pk = telephony_db.upsert_call_started(
        automation_id=automation.id,
        client_id=automation.client_id,
        vapi_call_id=vapi_call_id,
        direction=call.get("type") or call.get("direction") or "inbound",
        caller_e164=customer.get("number"),
        callee_e164=callee.get("number"),
    )

    # Ended state.
    ended_reason = message.get("endedReason") or call.get("endedReason")
    started_at_iso = call.get("startedAt") or call.get("createdAt")
    ended_at_iso = call.get("endedAt") or message.get("endedAt")
    duration = _safe_duration_seconds(started_at_iso, ended_at_iso)
    recording_url = message.get("recordingUrl") or call.get("recordingUrl")
    summary = message.get("summary") or message.get("analysis", {}).get("summary")
    status_value = "completed" if duration and duration > 5 else "dropped"

    telephony_db.update_call_ended(
        vapi_call_id=vapi_call_id,
        status=status_value,
        ended_reason=ended_reason,
        duration_seconds=duration,
        recording_url=recording_url,
        summary=summary,
        extras={"ended_payload": message},
    )

    # Transcript.
    transcript_chunks = _normalise_transcript(message)
    if transcript_chunks:
        telephony_db.bulk_insert_transcript(call_id=call_pk, chunks=transcript_chunks)

    # Outcome — only if not already set by a tool call (booked / callback_requested).
    # If still 'unknown' here, mark as 'dropped' for short calls or 'declined' otherwise.
    if status_value == "dropped":
        telephony_db.update_call_outcome(vapi_call_id=vapi_call_id, outcome="dropped")


# ─── Helpers ────────────────────────────────────────────────────────────────

def _verify_secret(request: Request) -> None:
    if not WEBHOOK_SECRET:
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bad webhook secret")


def _automation_for_call(call: dict[str, Any]) -> TelephonyAutomation:
    """Find which telephony automation this call belongs to."""
    assistant_id = (call.get("assistant") or {}).get("id") or call.get("assistantId")
    if assistant_id:
        a = telephony_db.find_automation_by_vapi_assistant_id(assistant_id)
        if a:
            return a
    automations = telephony_db.load_active_telephony_automations()
    if not automations:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="no active telephony automation")
    return automations[0]


def _caller_e164_for(vapi_call_id: str | None) -> str | None:
    """Look up caller_e164 we previously stored for this call."""
    if not vapi_call_id:
        return None
    # Cheap lookup — read straight from calls table.
    import psycopg
    with psycopg.connect(os.getenv("DATABASE_URL")) as conn, conn.cursor() as cur:
        cur.execute("SELECT caller_e164 FROM calls WHERE vapi_call_id = %s", (vapi_call_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _call_pk_for(vapi_call_id: str | None) -> int | None:
    if not vapi_call_id:
        return None
    import psycopg
    with psycopg.connect(os.getenv("DATABASE_URL")) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM calls WHERE vapi_call_id = %s", (vapi_call_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _safe_duration_seconds(started_iso: str | None, ended_iso: str | None) -> int | None:
    if not (started_iso and ended_iso):
        return None
    try:
        s = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_iso.replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds()))
    except Exception:
        return None


def _normalise_transcript(message: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Vapi puts the full transcript on the end-of-call-report message. The exact
    shape varies by SDK version — we accept either:
      - message.messages       = [{role, message, time?}]
      - message.transcript     = [{role, content, time?}]
      - message.artifact.messages = [...]
    """
    candidates = (
        message.get("messages")
        or message.get("transcript")
        or (message.get("artifact") or {}).get("messages")
        or []
    )
    out: list[dict[str, Any]] = []
    for m in candidates:
        role = m.get("role") or "assistant"
        content = m.get("message") or m.get("content") or ""
        spoken_at = m.get("time") or m.get("createdAt")
        if not content:
            continue
        out.append({"role": role, "content": content, "spoken_at": spoken_at})
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ─── CLI entrypoint ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("uvicorn not installed. Run: pip install fastapi uvicorn")
    port = int(os.getenv("TELEPHONY_PORT", "8088"))
    uvicorn.run(app, host="0.0.0.0", port=port)
