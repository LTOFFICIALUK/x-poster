"""
automation_db.py
=================
Database access layer for the Teventis automation runtime.

Everything client-/automation-specific is loaded from Postgres at startup.
Credentials are decrypted on read using the master key from TEVENTIS_SECRETS_KEY
via Postgres pgcrypto (`pgp_sym_decrypt`). Plaintext secrets never leave the DB.

This module is intentionally thin and synchronous — psycopg connection-per-op
is cheap for our scale (3 posts/day per client) and avoids pooling complexity.

Public surface:
    load_active_automations(type='news_posting') -> list[Automation]
    create_run(automation_id, trigger_source) -> int
    finalise_run(run_id, status, articles_considered, article_id_chosen, notes)
    upsert_article(automation_id, client_id, article, selection_status) -> int
    article_already_seen(automation_id, url) -> bool
    record_post(automation_id, client_id, article_id, platform, status,
                generated_text, external_id, error_message) -> int
    log(level, message, run_id=None, automation_id=None, client_id=None, context=None)
"""

from __future__ import annotations

import os
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


# ─── Module-level config ─────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL")
SECRETS_KEY  = os.getenv("TEVENTIS_SECRETS_KEY")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set. Add it to .env.")
if not SECRETS_KEY:
    raise SystemExit(
        "TEVENTIS_SECRETS_KEY is not set. This is the master key used to decrypt "
        "social_account_credentials. Generate one with `openssl rand -hex 32` and "
        "store it in .env. The same key must have been used to encrypt the rows."
    )


def _connect() -> psycopg.Connection:
    """Open a fresh connection. Caller is responsible for closing (use `with`)."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ─── Dataclasses returned to the caller ──────────────────────────────────────

@dataclass
class SocialAccount:
    id: int
    platform: str                          # 'twitter' | 'linkedin' | 'instagram'
    handle: str | None
    mode: str | None                       # 'official' | 'unofficial' | None
    status: str                            # 'active' | 'disabled' | 'flagged'
    metadata: dict[str, Any]               # platform_metadata JSONB
    credentials: dict[str, str] = field(default_factory=dict)  # decrypted key→value
    enabled_for_automation: bool = True    # from the junction table


@dataclass
class NewsAutomationConfig:
    article_requirement_prompt: str
    exclude_keywords: list[str]
    require_keywords: list[str]
    trusted_sources: list[str]
    post_style_instructions: dict[str, str]
    max_articles_per_fetch: int
    fallback_image_url: str | None
    extras: dict[str, Any]


@dataclass
class Automation:
    id: int
    client_id: int
    client_slug: str
    client_name: str
    type: str                              # 'news_posting' for now
    name: str
    status: str                            # 'active' | 'paused' | 'archived'
    timezone: str                          # IANA tz, e.g. 'Europe/London'
    dry_run: bool
    schedule_times: list[str]              # e.g. ['08:00', '13:00', '18:00']
    config: NewsAutomationConfig | None    # None for non-news automation types
    social_accounts: list[SocialAccount]


# ─── Loaders ─────────────────────────────────────────────────────────────────

def load_active_automations(type_filter: str = "news_posting") -> list[Automation]:
    """
    Load every active automation of the requested type, with its config,
    its enabled social accounts, and the decrypted credentials for each.

    A returned automation is fully self-contained — the orchestrator does not
    need to make further DB calls just to publish a post.
    """
    automations: list[Automation] = []

    with _connect() as conn, conn.cursor() as cur:
        # 1. Automations + client + news config (LEFT JOIN — non-news types still load)
        cur.execute("""
            SELECT
              a.id            AS automation_id,
              a.client_id     AS client_id,
              c.slug          AS client_slug,
              c.name          AS client_name,
              a.type          AS type,
              a.name          AS name,
              a.status        AS status,
              a.timezone      AS timezone,
              a.dry_run       AS dry_run,
              a.schedule_times AS schedule_times,
              nac.article_requirement_prompt,
              nac.exclude_keywords,
              nac.require_keywords,
              nac.trusted_sources,
              nac.post_style_instructions,
              nac.max_articles_per_fetch,
              nac.fallback_image_url,
              nac.extras                   AS news_extras
            FROM automations a
            JOIN clients c ON c.id = a.client_id
            LEFT JOIN news_automation_configs nac ON nac.automation_id = a.id
            WHERE a.status = 'active'
              AND c.status = 'active'
              AND a.type   = %s
            ORDER BY c.slug, a.name
        """, (type_filter,))
        rows = cur.fetchall()

        for row in rows:
            config = None
            if type_filter == "news_posting" and row["article_requirement_prompt"] is not None:
                config = NewsAutomationConfig(
                    article_requirement_prompt=row["article_requirement_prompt"] or "",
                    exclude_keywords=list(row["exclude_keywords"] or []),
                    require_keywords=list(row["require_keywords"] or []),
                    trusted_sources=list(row["trusted_sources"] or []),
                    post_style_instructions=row["post_style_instructions"] or {},
                    max_articles_per_fetch=row["max_articles_per_fetch"] or 25,
                    fallback_image_url=row["fallback_image_url"],
                    extras=row["news_extras"] or {},
                )

            automations.append(Automation(
                id=row["automation_id"],
                client_id=row["client_id"],
                client_slug=row["client_slug"],
                client_name=row["client_name"],
                type=row["type"],
                name=row["name"],
                status=row["status"],
                timezone=row["timezone"],
                dry_run=row["dry_run"],
                schedule_times=list(row["schedule_times"] or []),
                config=config,
                social_accounts=[],
            ))

        if not automations:
            return []

        # 2. Social accounts per automation, with decrypted credentials.
        automation_ids = [a.id for a in automations]
        cur.execute("""
            SELECT
              asa.automation_id,
              sa.id           AS social_account_id,
              sa.platform,
              sa.handle,
              sa.mode,
              sa.status       AS sa_status,
              sa.platform_metadata,
              asa.enabled     AS enabled_for_automation
            FROM automation_social_accounts asa
            JOIN social_accounts sa ON sa.id = asa.social_account_id
            WHERE asa.automation_id = ANY(%s)
              AND asa.enabled = true
              AND sa.status = 'active'
            ORDER BY asa.automation_id, sa.platform
        """, (automation_ids,))
        sa_rows = cur.fetchall()

        if sa_rows:
            sa_ids = [r["social_account_id"] for r in sa_rows]
            cur.execute("""
                SELECT
                  social_account_id,
                  key,
                  pgp_sym_decrypt(value_encrypted, %s)::text AS value
                FROM social_account_credentials
                WHERE social_account_id = ANY(%s)
            """, (SECRETS_KEY, sa_ids))
            cred_rows = cur.fetchall()

            creds_by_sa: dict[int, dict[str, str]] = {}
            for cr in cred_rows:
                creds_by_sa.setdefault(cr["social_account_id"], {})[cr["key"]] = cr["value"]

            sa_by_automation: dict[int, list[SocialAccount]] = {}
            for r in sa_rows:
                sa = SocialAccount(
                    id=r["social_account_id"],
                    platform=r["platform"],
                    handle=r["handle"],
                    mode=r["mode"],
                    status=r["sa_status"],
                    metadata=r["platform_metadata"] or {},
                    credentials=creds_by_sa.get(r["social_account_id"], {}),
                    enabled_for_automation=r["enabled_for_automation"],
                )
                sa_by_automation.setdefault(r["automation_id"], []).append(sa)

            for a in automations:
                a.social_accounts = sa_by_automation.get(a.id, [])

    return automations


# ─── Run lifecycle ───────────────────────────────────────────────────────────

def create_run(automation_id: int, client_id: int, trigger_source: str = "scheduled") -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO runs (automation_id, client_id, trigger_source, status)
            VALUES (%s, %s, %s, 'running')
            RETURNING id
        """, (automation_id, client_id, trigger_source))
        run_id = cur.fetchone()["id"]
        conn.commit()
        return run_id


def finalise_run(
    run_id: int,
    status: str,                       # 'success' | 'partial' | 'failed'
    articles_considered: int = 0,
    article_id_chosen: int | None = None,
    notes: str | None = None,
) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE runs
               SET finished_at         = NOW(),
                   status              = %s,
                   articles_considered = %s,
                   article_id_chosen   = %s,
                   notes               = %s
             WHERE id = %s
        """, (status, articles_considered, article_id_chosen, notes, run_id))
        conn.commit()


# ─── Articles & deduplication ────────────────────────────────────────────────

def article_already_seen(automation_id: int, url: str) -> bool:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM articles
            WHERE automation_id = %s AND url = %s
            LIMIT 1
        """, (automation_id, url))
        return cur.fetchone() is not None


def upsert_article(
    automation_id: int,
    client_id: int,
    article: dict[str, Any],
    selection_status: str = "considered",
) -> int:
    """Insert or update an article row; returns articles.id."""
    source_name  = (article.get("source") or {}).get("name")
    title        = article.get("title") or ""
    description  = article.get("description")
    url          = article.get("url")
    image_url    = article.get("urlToImage")
    published_at = article.get("publishedAt")

    if not url:
        raise ValueError("Article has no URL — cannot persist.")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO articles (
                automation_id, client_id,
                source_name, title, description, url, image_url, published_at,
                selection_status, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (automation_id, url) DO UPDATE
                SET selection_status = EXCLUDED.selection_status,
                    metadata         = EXCLUDED.metadata
            RETURNING id
        """, (
            automation_id, client_id,
            source_name, title, description, url, image_url, published_at,
            selection_status, json.dumps({}),
        ))
        article_id = cur.fetchone()["id"]
        conn.commit()
        return article_id


# ─── Posts ────────────────────────────────────────────────────────────────────

def record_post(
    automation_id: int,
    client_id: int,
    article_id: int,
    platform: str,
    status: str,                      # 'published' | 'failed' | 'skipped' | 'proposed'
    generated_text: str,
    external_id: str | None = None,
    error_message: str | None = None,
) -> int:
    posted_at = "NOW()" if status == "published" else None
    with _connect() as conn, conn.cursor() as cur:
        if posted_at:
            cur.execute("""
                INSERT INTO posts (
                    automation_id, client_id, article_id, platform, status,
                    generated_text, posted_at, external_id, error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s)
                RETURNING id
            """, (
                automation_id, client_id, article_id, platform, status,
                generated_text, external_id, error_message,
            ))
        else:
            cur.execute("""
                INSERT INTO posts (
                    automation_id, client_id, article_id, platform, status,
                    generated_text, external_id, error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                automation_id, client_id, article_id, platform, status,
                generated_text, external_id, error_message,
            ))
        post_id = cur.fetchone()["id"]
        conn.commit()
        return post_id


# ─── Logs ─────────────────────────────────────────────────────────────────────

def log(
    level: str,                       # 'info' | 'warning' | 'error'
    message: str,
    run_id: int | None = None,
    automation_id: int | None = None,
    client_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """
    Append a structured log row. Best-effort — DB log failures must never break
    the actual automation, so this swallows exceptions and prints to stderr.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO logs (run_id, automation_id, client_id, level, message, context)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """, (run_id, automation_id, client_id, level, message,
                  json.dumps(context or {})))
            conn.commit()
    except Exception as e:
        import sys
        print(f"[automation_db.log] failed to persist log: {e}", file=sys.stderr)
