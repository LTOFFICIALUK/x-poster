"""
vapi_assistant.py
==================
One-shot deploy script that pushes Riley's assistant config to Vapi.

What it does:
  - For every active telephony automation in the DB, build the canonical
    assistant payload (system prompt from riley_prompt.py, voice, function
    tool definitions, opening line).
  - If the automation already has a vapi_assistant_id, PATCH it.
  - If it doesn't, CREATE a new assistant and persist the id.

Usage:
    # update existing assistants in place
    python vapi_assistant.py upload

    # show the payload that would be sent (no API call)
    python vapi_assistant.py print

    # show + diff against what's live
    python vapi_assistant.py diff

The webhook URL the assistant points at is read from $TELEPHONY_WEBHOOK_URL
(e.g. https://riley.teventis.com/vapi/webhook). Vapi will POST every call
event there.
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

import json

from dotenv import load_dotenv
load_dotenv()

import telephony_db
from telephony_db import TelephonyAutomation
from vapi_client import VapiClient, VapiError
from riley_prompt import SYSTEM_PROMPT, FIRST_MESSAGE


WEBHOOK_URL = os.getenv("TELEPHONY_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("TELEPHONY_WEBHOOK_SECRET", "")


# ─── Tool definitions exposed to Riley ──────────────────────────────────────
# Vapi mirrors OpenAI's function-tool schema. Riley invokes these mid-call
# and our /vapi/webhook returns a result that gets piped back into the LLM.

FUNCTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_availability",
            "description": (
                "Fetch the next set of bookable Calendly slots for Rio's discovery call. "
                "Use this BEFORE offering specific times to the caller."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum slots to return (default 6).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_slot",
            "description": (
                "Book a discovery call into Rio's Calendly at the chosen ISO start time, "
                "and SMS the caller a single-use confirmation link. Call this only after "
                "the caller has agreed to a specific time and given an email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_iso": {
                        "type": "string",
                        "description": "ISO-8601 start time the caller chose, e.g. 2026-05-12T14:00:00Z.",
                    },
                    "caller_name":     {"type": "string"},
                    "caller_email":    {"type": "string"},
                    "caller_business": {"type": "string"},
                    "caller_region":   {"type": "string"},
                    "service_interest": {
                        "type": "string",
                        "enum": [
                            "lead-recovery",
                            "lead-generation",
                            "automated-followup",
                            "ai-appointment-booking",
                            "chat-assistant",
                            "unsure",
                        ],
                    },
                },
                "required": ["start_iso", "caller_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_callback_request",
            "description": (
                "Log a request for Rio to call back later, and SMS Rio's mobile. Use this "
                "for: out-of-UK callers, existing clients with support issues, or when you "
                "can't confidently handle the call. Captures the basics so Rio can pick up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name":  {"type": "string"},
                    "caller_email": {"type": "string"},
                    "reason":       {"type": "string", "description": "One-line note on why Rio's calling back."},
                },
                "required": ["reason"],
            },
        },
    },
]


# ─── Build the assistant payload ────────────────────────────────────────────

def build_assistant_payload(automation: TelephonyAutomation) -> dict:
    cfg = automation.config
    system_prompt = cfg.system_prompt_override or SYSTEM_PROMPT
    first_message = cfg.opening_line or FIRST_MESSAGE

    return {
        "name": f"Riley — {automation.client_slug}",
        # First-message behaviour: speak our opening line BEFORE the model talks.
        "firstMessage": first_message,
        "firstMessageMode": "assistant-speaks-first",

        "model": {
            # TODO: confirm Vapi accepts Anthropic models directly. If not, route
            # through Vapi's "anthropic" provider with the appropriate model id.
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system_prompt},
            ],
            "tools": FUNCTION_TOOLS,
        },

        "voice": {
            # TODO: pick a specific voice in week 1 of testing. ElevenLabs is the
            # most natural-sounding for UK English; default voiceIds are stable.
            # Placeholder: a Vapi-built-in female UK voice. Verify the id.
            "provider": "11labs",
            "voiceId": "EXAVITQu4vr4xnSDxMaL",   # placeholder — pick during voice tests
            "stability": 0.5,
            "similarityBoost": 0.75,
        },

        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en-GB",
        },

        "endCallFunctionEnabled": True,
        "recordingEnabled": cfg.recording_enabled,

        # Server (webhook) settings — Vapi POSTs every event here.
        "serverUrl": WEBHOOK_URL,
        "serverUrlSecret": WEBHOOK_SECRET or None,

        # Light guardrail: cap calls at 6 minutes — this is a booking line, not therapy.
        "maxDurationSeconds": 360,
        "silenceTimeoutSeconds": 30,
        "responseDelaySeconds": 0.4,
        "llmRequestDelaySeconds": 0.1,
    }


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_print() -> int:
    automations = telephony_db.load_active_telephony_automations()
    if not automations:
        print("No active telephony automations found.")
        return 1
    for a in automations:
        print(f"\n# Automation #{a.id} — {a.client_slug} — {a.name}")
        print(json.dumps(build_assistant_payload(a), indent=2))
    return 0


def cmd_upload() -> int:
    automations = telephony_db.load_active_telephony_automations()
    if not automations:
        print("No active telephony automations found. Insert one first via the admin or seed script.")
        return 1

    if not WEBHOOK_URL:
        print("ERROR: TELEPHONY_WEBHOOK_URL is not set. Set it before uploading "
              "(otherwise Vapi will have no webhook target).")
        return 2

    vapi = VapiClient()
    try:
        for a in automations:
            payload = build_assistant_payload(a)
            existing_id = a.config.vapi_assistant_id
            try:
                if existing_id:
                    print(f"Updating Vapi assistant {existing_id} for automation #{a.id} ({a.client_slug}) ...")
                    result = vapi.update_assistant(existing_id, payload)
                else:
                    print(f"Creating Vapi assistant for automation #{a.id} ({a.client_slug}) ...")
                    result = vapi.create_assistant(payload)
                    telephony_db.set_vapi_identifiers(
                        automation_id=a.id,
                        vapi_assistant_id=result.id,
                    )
                print(f"  ok — assistant id = {result.id}")
            except VapiError as e:
                print(f"  FAILED: {e}")
    finally:
        vapi.close()
    return 0


def cmd_diff() -> int:
    automations = telephony_db.load_active_telephony_automations()
    vapi = VapiClient()
    try:
        for a in automations:
            local = build_assistant_payload(a)
            print(f"\n# Automation #{a.id} — {a.client_slug}")
            if not a.config.vapi_assistant_id:
                print("  (no vapi_assistant_id yet — would CREATE)")
                continue
            try:
                live = vapi.get_assistant(a.config.vapi_assistant_id)
            except VapiError as e:
                print(f"  could not fetch live assistant: {e}")
                continue
            for k in ("name", "firstMessage", "model", "voice", "serverUrl",
                      "recordingEnabled", "maxDurationSeconds"):
                lv = local.get(k)
                rv = live.get(k)
                same = lv == rv
                print(f"  {k}: {'OK' if same else 'DIFF'}")
                if not same:
                    print(f"    local: {json.dumps(lv)[:200]}")
                    print(f"    live:  {json.dumps(rv)[:200]}")
    finally:
        vapi.close()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("print", "upload", "diff"):
        print("Usage: python vapi_assistant.py [print|upload|diff]")
        return 64
    return {"print": cmd_print, "upload": cmd_upload, "diff": cmd_diff}[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
