"""
telephony_db.py
================
DB access layer for the telephony-appointments automation. Mirrors the shape
of `automation_db.py` (synchronous psycopg, dict_row, connection-per-op) so the
two layers feel like the same codebase.

Public surface:
    load_telephony_automation(automation_id) -> TelephonyAutomation | None
    load_active_telephony_automations()      -> list[TelephonyAutomation]
    upsert_call_started(...)                 -> int
    update_call_ended(...)                   -> None
    update_call_outcome(...)                 -> None
    insert_transcript_chunk(...)             -> int
    insert_callback_request(...)             -> int
    update_callback_request_status(...)      -> None

The dataclasses returned here are read-only snapshots — mutate the DB through
the helper functions, then re-load if you need fresh state.
"""

from __future__ import annotations

# ── sys.path bootstrap ──────────────────────────────────────────────────────
# This file lives in `teventis-automations/telephony-appointments/`. To import
# `automation_db` (which lives in the parent dir), add the parent to sys.path.
# Also add this dir so peer modules can be imported flat.
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# ────────────────────────────────────────────────────────────────────────────

import json
from dataclasses import dataclass, field
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as e:
    raise SystemExit(
        "psycopg not installed. Run:  pip install 'psycopg[binary]'"
    ) from e


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set. Add it to .env.")


def _connect() -> psycopg.Connection:
    """Open a fresh connection. Caller is responsible for closing (use `with`)."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class TelephonyConfig:
    automation_id: int
    vapi_assistant_id: str | None
    vapi_phone_number_id: str | None
    vapi_phone_number_e164: str | None
    calendly_event_url: str
    calendly_user_uri: str | None
    calendly_event_type_uri: str | None
    escalation_phone_e164: str | None
    opening_line: str
    system_prompt_override: str | None
    recording_enabled: bool
    recording_retention_days: int
    transcript_retention_days: int
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class TelephonyAutomation:
    id: int
    client_id: int
    client_slug: str
    client_name: str
    name: str
    status: str            # 'active' | 'paused' | 'archived'
    timezone: str
    config: TelephonyConfig


# ─── Loaders ─────────────────────────────────────────────────────────────────

def load_telephony_automation(automation_id: int) -> TelephonyAutomation | None:
    """Load a single telephony automation by id, with its config + client."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
              a.id              AS automation_id,
              a.client_id       AS client_id,
              c.slug            AS client_slug,
              c.name            AS client_name,
              a.name            AS name,
              a.status          AS status,
              a.timezone        AS timezone,
              tac.vapi_assistant_id,
              tac.vapi_phone_number_id,
              tac.vapi_phone_number_e164,
              tac.calendly_event_url,
              tac.calendly_user_uri,
              tac.calendly_event_type_uri,
              tac.escalation_phone_e164,
              tac.opening_line,
              tac.system_prompt_override,
              tac.recording_enabled,
              tac.recording_retention_days,
              tac.transcript_retention_days,
              tac.extras                AS tac_extras
            FROM automations a
            JOIN clients c ON c.id = a.client_id
            JOIN telephony_automation_configs tac ON tac.automation_id = a.id
            WHERE a.id   = %s
              AND a.type = 'telephony_appointments'
            LIMIT 1
        """, (automation_id,))
        row = cur.fetchone()
        return _row_to_automation(row) if row else None


def load_active_telephony_automations() -> list[TelephonyAutomation]:
    """Load every active telephony automation. Used by the assistant uploader."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
              a.id              AS automation_id,
              a.client_id       AS client_id,
              c.slug            AS client_slug,
              c.name            AS client_name,
              a.name            AS name,
              a.status          AS status,
              a.timezone        AS timezone,
              tac.vapi_assistant_id,
              tac.vapi_phone_number_id,
              tac.vapi_phone_number_e164,
              tac.calendly_event_url,
              tac.calendly_user_uri,
              tac.calendly_event_type_uri,
              tac.escalation_phone_e164,
              tac.opening_line,
              tac.system_prompt_override,
              tac.recording_enabled,
              tac.recording_retention_days,
              tac.transcript_retention_days,
              tac.extras                AS tac_extras
            FROM automations a
            JOIN clients c ON c.id = a.client_id
            JOIN telephony_automation_configs tac ON tac.automation_id = a.id
            WHERE a.status = 'active'
              AND c.status = 'active'
              AND a.type   = 'telephony_appointments'
            ORDER BY c.slug, a.name
        """)
        rows = cur.fetchall()
        return [_row_to_automation(r) for r in rows]


def find_automation_by_vapi_assistant_id(vapi_assistant_id: str) -> TelephonyAutomation | None:
    """Reverse lookup used by the webhook: given Vapi's assistant id, who is this?"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.id AS id
              FROM automations a
              JOIN telephony_automation_configs tac ON tac.automation_id = a.id
             WHERE tac.vapi_assistant_id = %s
               AND a.type = 'telephony_appointments'
             LIMIT 1
        """, (vapi_assistant_id,))
        row = cur.fetchone()
    return load_telephony_automation(row["id"]) if row else None


def _row_to_automation(row: dict[str, Any]) -> TelephonyAutomation:
    config = TelephonyConfig(
        automation_id=row["automation_id"],
        vapi_assistant_id=row["vapi_assistant_id"],
        vapi_phone_number_id=row["vapi_phone_number_id"],
        vapi_phone_number_e164=row["vapi_phone_number_e164"],
        calendly_event_url=row["calendly_event_url"],
        calendly_user_uri=row["calendly_user_uri"],
        calendly_event_type_uri=row["calendly_event_type_uri"],
        escalation_phone_e164=row["escalation_phone_e164"],
        opening_line=row["opening_line"] or "",
        system_prompt_override=row["system_prompt_override"],
        recording_enabled=row["recording_enabled"],
        recording_retention_days=row["recording_retention_days"],
        transcript_retention_days=row["transcript_retention_days"],
        extras=row["tac_extras"] or {},
    )
    return TelephonyAutomation(
        id=row["automation_id"],
        client_id=row["client_id"],
        client_slug=row["client_slug"],
        client_name=row["client_name"],
        name=row["name"],
        status=row["status"],
        timezone=row["timezone"],
        config=config,
    )


# ─── Calls — lifecycle ───────────────────────────────────────────────────────

def upsert_call_started(
    *,
    automation_id: int,
    client_id: int,
    vapi_call_id: str,
    direction: str = "inbound",
    caller_e164: str | None = None,
    callee_e164: str | None = None,
) -> int:
    """
    Create or fetch a call row at call.started time. Idempotent on vapi_call_id
    so a duplicate webhook doesn't create a second row.
    Returns the calls.id.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO calls (
                automation_id, client_id, vapi_call_id,
                direction, caller_e164, callee_e164, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'in_progress')
            ON CONFLICT (vapi_call_id) DO UPDATE
                SET automation_id = EXCLUDED.automation_id,
                    client_id     = EXCLUDED.client_id
            RETURNING id
        """, (
            automation_id, client_id, vapi_call_id,
            direction, caller_e164, callee_e164,
        ))
        call_id = cur.fetchone()["id"]
        conn.commit()
        return call_id


def update_call_ended(
    *,
    vapi_call_id: str,
    status: str,                                # 'completed' | 'failed' | 'dropped'
    ended_reason: str | None = None,
    duration_seconds: int | None = None,
    recording_url: str | None = None,
    recording_expires_at_iso: str | None = None,
    summary: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Patch the row when Vapi reports end-of-call."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE calls
               SET ended_at             = NOW(),
                   status               = %s,
                   ended_reason         = %s,
                   duration_seconds     = %s,
                   recording_url        = COALESCE(%s, recording_url),
                   recording_expires_at = COALESCE(%s::timestamptz, recording_expires_at),
                   summary              = COALESCE(%s, summary),
                   extras               = CASE
                                            WHEN %s::jsonb IS NULL THEN extras
                                            ELSE extras || %s::jsonb
                                          END
             WHERE vapi_call_id = %s
        """, (
            status, ended_reason, duration_seconds,
            recording_url, recording_expires_at_iso, summary,
            json.dumps(extras) if extras else None,
            json.dumps(extras) if extras else None,
            vapi_call_id,
        ))
        conn.commit()


def update_call_captured_fields(
    *,
    vapi_call_id: str,
    caller_name: str | None = None,
    caller_business: str | None = None,
    caller_email: str | None = None,
    caller_region: str | None = None,
    service_interest: str | None = None,
) -> None:
    """Populate the qualification fields as Riley collects them."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE calls
               SET caller_name      = COALESCE(%s, caller_name),
                   caller_business  = COALESCE(%s, caller_business),
                   caller_email     = COALESCE(%s, caller_email),
                   caller_region    = COALESCE(%s, caller_region),
                   service_interest = COALESCE(%s, service_interest)
             WHERE vapi_call_id = %s
        """, (
            caller_name, caller_business, caller_email, caller_region,
            service_interest, vapi_call_id,
        ))
        conn.commit()


def update_call_outcome(
    *,
    vapi_call_id: str,
    outcome: str,                                # see calls.outcome CHECK
    calendly_event_uri: str | None = None,
    calendly_event_url: str | None = None,
    calendly_scheduled_at_iso: str | None = None,
    callback_request_id: int | None = None,
) -> None:
    """Set the final outcome of the call. Called from the function-tool handlers."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE calls
               SET outcome                  = %s,
                   calendly_event_uri       = COALESCE(%s, calendly_event_uri),
                   calendly_event_url       = COALESCE(%s, calendly_event_url),
                   calendly_scheduled_at    = COALESCE(%s::timestamptz, calendly_scheduled_at),
                   callback_request_id      = COALESCE(%s, callback_request_id)
             WHERE vapi_call_id = %s
        """, (
            outcome, calendly_event_uri, calendly_event_url,
            calendly_scheduled_at_iso, callback_request_id, vapi_call_id,
        ))
        conn.commit()


# ─── Transcripts ─────────────────────────────────────────────────────────────

def insert_transcript_chunk(
    *,
    call_id: int,
    role: str,                                    # 'assistant' | 'user' | 'system' | 'tool'
    content: str,
    spoken_at_iso: str | None = None,
) -> int:
    """Append one transcript line. spoken_at_iso defaults to NOW() if omitted."""
    with _connect() as conn, conn.cursor() as cur:
        if spoken_at_iso:
            cur.execute("""
                INSERT INTO call_transcripts (call_id, role, content, spoken_at)
                VALUES (%s, %s, %s, %s::timestamptz)
                RETURNING id
            """, (call_id, role, content, spoken_at_iso))
        else:
            cur.execute("""
                INSERT INTO call_transcripts (call_id, role, content)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (call_id, role, content))
        chunk_id = cur.fetchone()["id"]
        conn.commit()
        return chunk_id


def bulk_insert_transcript(
    *,
    call_id: int,
    chunks: list[dict[str, Any]],   # [{role, content, spoken_at?}]
) -> int:
    """Insert a whole transcript at once (used at end-of-call). Returns rows inserted."""
    if not chunks:
        return 0
    with _connect() as conn, conn.cursor() as cur:
        for chunk in chunks:
            cur.execute("""
                INSERT INTO call_transcripts (call_id, role, content, spoken_at)
                VALUES (%s, %s, %s, COALESCE(%s::timestamptz, NOW()))
            """, (
                call_id,
                chunk.get("role", "assistant"),
                chunk.get("content", ""),
                chunk.get("spoken_at"),
            ))
        conn.commit()
    return len(chunks)


# ─── Callback requests ───────────────────────────────────────────────────────

def insert_callback_request(
    *,
    automation_id: int,
    client_id: int,
    caller_e164: str,
    call_id: int | None = None,
    caller_name: str | None = None,
    caller_email: str | None = None,
    reason_summary: str | None = None,
) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO callback_requests (
                call_id, automation_id, client_id,
                caller_name, caller_e164, caller_email, reason_summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            call_id, automation_id, client_id,
            caller_name, caller_e164, caller_email, reason_summary,
        ))
        cb_id = cur.fetchone()["id"]
        conn.commit()
        return cb_id


def update_callback_request_status(
    *,
    callback_request_id: int,
    status: str,                                 # 'pending' | 'completed' | 'cancelled'
    notes: str | None = None,
) -> None:
    completed_at_clause = "completed_at = NOW(), " if status == "completed" else ""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"""
            UPDATE callback_requests
               SET status = %s,
                   {completed_at_clause}
                   notes  = COALESCE(%s, notes)
             WHERE id = %s
        """, (status, notes, callback_request_id))
        conn.commit()


# ─── Persisted Vapi assistant id ────────────────────────────────────────────

def set_vapi_identifiers(
    *,
    automation_id: int,
    vapi_assistant_id: str | None = None,
    vapi_phone_number_id: str | None = None,
    vapi_phone_number_e164: str | None = None,
) -> None:
    """Used by vapi_assistant.py after a successful upload/provision."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE telephony_automation_configs
               SET vapi_assistant_id      = COALESCE(%s, vapi_assistant_id),
                   vapi_phone_number_id   = COALESCE(%s, vapi_phone_number_id),
                   vapi_phone_number_e164 = COALESCE(%s, vapi_phone_number_e164)
             WHERE automation_id = %s
        """, (
            vapi_assistant_id, vapi_phone_number_id, vapi_phone_number_e164,
            automation_id,
        ))
        conn.commit()


def set_calendly_uris(
    *,
    automation_id: int,
    user_uri: str,
    event_type_uri: str,
) -> None:
    """Cache the Calendly user + event-type URIs after first resolve."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE telephony_automation_configs
               SET calendly_user_uri       = %s,
                   calendly_event_type_uri = %s
             WHERE automation_id = %s
        """, (user_uri, event_type_uri, automation_id))
        conn.commit()
