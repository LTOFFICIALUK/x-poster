"""
News Automation — DB-driven runtime
====================================
Fetches top world news, generates AI-powered neutral summaries, and posts to
the platforms each client has configured. Every client-specific knob is loaded
from Postgres on every scheduler tick — there is nothing per-client in this file.

What lives where:
    - Per-automation config + schedule_times + timezone + dry_run    → DB (automations, news_automation_configs)
    - Per-client social handles, account IDs, IG mode, session paths → DB (social_accounts.platform_metadata)
    - Per-client API keys / passwords / tokens                       → DB (social_account_credentials, encrypted)
    - Article history / dedup                                        → DB (articles)
    - Run + post history                                             → DB (runs, posts)
    - AI model name / max_tokens                                      → hardcoded here (intentional — internal testing parity)
    - NewsAPI + Anthropic API keys                                    → .env (shared across all clients)

Author: Luke Carter — AI Automation Specialist
"""

from __future__ import annotations

import os
import sys
import time
import logging
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()  # must happen BEFORE importing automation_db (it reads env)

import requests
import tweepy
from newsapi import NewsApiClient
import anthropic

import automation_db
from automation_db import (
    Automation,
    SocialAccount,
    load_active_automations,
    create_run,
    finalise_run,
    upsert_article,
    article_already_seen,
    record_post,
    log as db_log,
)


# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("automation.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ─── Hardcoded (deliberately) ────────────────────────────────────────────────
# Per Luke: keep AI choice consistent across clients for internal testing parity.
# When you want this to vary per client, move to news_automation_configs.

AI_MODEL      = "claude-haiku-4-5-20251001"
AI_MAX_TOKENS = 400


# ─── Shared env (NOT per-client) ─────────────────────────────────────────────

NEWS_API_KEY      = os.getenv("NEWS_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not NEWS_API_KEY or not ANTHROPIC_API_KEY:
    raise SystemExit(
        "NEWS_API_KEY and ANTHROPIC_API_KEY must be set in .env. "
        "These two are global (shared across all clients)."
    )


# ─── News fetch ──────────────────────────────────────────────────────────────

def fetch_top_news(automation: Automation) -> list[dict]:
    """
    Fetch articles from NewsAPI using this automation's trusted_sources +
    keyword filters, skipping anything already seen by this automation.
    """
    cfg = automation.config
    if cfg is None:
        return []

    page_size = max(min(cfg.max_articles_per_fetch, 100), 1)
    sources   = ",".join(cfg.trusted_sources) if cfg.trusted_sources else None

    try:
        client = NewsApiClient(api_key=NEWS_API_KEY)
        # If no trusted sources configured, fall back to top global headlines
        # rather than failing. Admin should populate trusted_sources soon.
        kwargs = {"language": "en", "page_size": page_size}
        if sources:
            kwargs["sources"] = sources
        response = client.get_top_headlines(**kwargs)
        articles = response.get("articles", [])
    except Exception as e:
        log.error(f"[{automation.client_slug}] NewsAPI fetch failed: {e}")
        db_log("error", f"NewsAPI fetch failed: {e}",
               automation_id=automation.id, client_id=automation.client_id)
        return []

    excludes = [k.lower() for k in cfg.exclude_keywords]
    requires = [k.lower() for k in cfg.require_keywords]

    filtered: list[dict] = []
    for a in articles:
        title = a.get("title") or ""
        desc  = a.get("description") or ""
        url   = a.get("url") or ""
        if not (title and desc and url):
            continue
        if title == "[Removed]":
            continue
        if article_already_seen(automation.id, url):
            continue

        haystack = (title + " " + desc).lower()
        if excludes and any(kw in haystack for kw in excludes):
            continue
        if requires and not any(kw in haystack for kw in requires):
            continue

        filtered.append(a)

    log.info(f"[{automation.client_slug}] Fetched {len(filtered)} candidate articles "
             f"(out of {len(articles)} from NewsAPI).")
    return filtered


# ─── AI post generation ──────────────────────────────────────────────────────

def generate_post(article: dict, platform: str, automation: Automation) -> str | None:
    """Use Claude to draft a neutral, platform-appropriate post."""
    cfg = automation.config
    if cfg is None:
        return None

    title       = article.get("title", "")
    description = article.get("description", "")
    source      = (article.get("source") or {}).get("name", "Unknown source")
    url         = article.get("url", "")

    instructions = cfg.post_style_instructions.get(platform)
    if not instructions:
        log.warning(f"[{automation.client_slug}] No post_style_instructions for {platform} — "
                    f"using a generic fallback.")
        instructions = "Write a neutral, factual social post about this article. End with the URL."

    requirement_prompt = cfg.article_requirement_prompt or (
        "You are a neutral world news summariser. Report facts only, no opinions, "
        "no partisan framing, no emotionally charged language."
    )

    prompt = f"""{requirement_prompt}

Article title: {title}
Article description: {description}
Source: {source}
URL: {url}

{instructions}

Return only the post text. No preamble, no explanation."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        log.info(f"[{automation.client_slug}] Generated {platform} post for: {title[:60]}...")
        return text
    except Exception as e:
        log.error(f"[{automation.client_slug}] AI generation failed for {platform}: {e}")
        db_log("error", f"AI generation failed for {platform}: {e}",
               automation_id=automation.id, client_id=automation.client_id,
               context={"platform": platform})
        return None


# ─── Posters ─────────────────────────────────────────────────────────────────
# Each poster takes (text, social_account, automation, image_url).
# They return (success: bool, external_id: str | None, error: str | None).

def post_to_twitter(text: str, sa: SocialAccount, automation: Automation, image_url: str | None) -> tuple[bool, str | None, str | None]:
    if automation.dry_run:
        log.info(f"[{automation.client_slug}] [DRY RUN] Twitter:\n{text}\n")
        return True, None, None

    creds = sa.credentials
    needed = ["api_key", "api_secret", "access_token", "access_secret"]
    missing = [k for k in needed if not creds.get(k)]
    if missing:
        return False, None, f"missing twitter credentials: {missing}"

    try:
        client = tweepy.Client(
            consumer_key       =creds["api_key"],
            consumer_secret    =creds["api_secret"],
            access_token       =creds["access_token"],
            access_token_secret=creds["access_secret"],
        )
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        log.info(f"[{automation.client_slug}] Posted to Twitter ({tweet_id}).")
        return True, str(tweet_id), None
    except tweepy.errors.TweepyException as e:
        return False, None, str(e)


def post_to_linkedin(text: str, sa: SocialAccount, automation: Automation, image_url: str | None) -> tuple[bool, str | None, str | None]:
    if automation.dry_run:
        log.info(f"[{automation.client_slug}] [DRY RUN] LinkedIn:\n{text}\n")
        return True, None, None

    access_token = sa.credentials.get("access_token")
    person_urn   = (sa.metadata or {}).get("person_urn")
    if not access_token or not person_urn:
        return False, None, "missing LinkedIn access_token or person_urn"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    try:
        r = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=payload)
        r.raise_for_status()
        urn = r.headers.get("x-restli-id")
        log.info(f"[{automation.client_slug}] Posted to LinkedIn ({urn or 'no urn'}).")
        return True, urn, None
    except requests.HTTPError as e:
        return False, None, f"{e} — {r.text if 'r' in locals() else ''}"
    except Exception as e:
        return False, None, str(e)


def post_to_instagram(text: str, sa: SocialAccount, automation: Automation, image_url: str | None) -> tuple[bool, str | None, str | None]:
    if automation.dry_run:
        log.info(f"[{automation.client_slug}] [DRY RUN] Instagram ({sa.mode}):\n{text}\n")
        return True, None, None

    if sa.mode == "unofficial":
        return _post_instagram_unofficial(text, sa, automation, image_url)
    return _post_instagram_official(text, sa, automation, image_url)


def _post_instagram_official(text: str, sa: SocialAccount, automation: Automation, image_url: str | None) -> tuple[bool, str | None, str | None]:
    access_token = sa.credentials.get("access_token")
    account_id   = (sa.metadata or {}).get("account_id")
    if not access_token or not account_id:
        return False, None, "missing instagram access_token or account_id"

    media_url = image_url or (automation.config.fallback_image_url if automation.config else None)
    if not media_url:
        return False, None, "no image_url and no fallback_image_url configured"

    try:
        create_url = f"https://graph.facebook.com/v18.0/{account_id}/media"
        cr = requests.post(create_url, params={
            "image_url":    media_url,
            "caption":      text,
            "access_token": access_token,
        })
        cr.raise_for_status()
        creation_id = cr.json().get("id")
        if not creation_id:
            return False, None, "no creation id from instagram /media"

        return _ig_wait_then_publish_creation(
            account_id=str(account_id),
            creation_id=str(creation_id),
            access_token=access_token,
            client_slug=automation.client_slug,
        )
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        return False, None, f"{e} — {body}"
    except Exception as e:
        return False, None, str(e)


# Per-account cached instagrapi clients keyed by social_account_id.
_IG_CLIENTS: dict[int, object] = {}


def _post_instagram_unofficial(text: str, sa: SocialAccount, automation: Automation, image_url: str | None) -> tuple[bool, str | None, str | None]:
    try:
        import httpx
        from instagrapi import Client
        from instagrapi.exceptions import LoginRequired

        username      = (sa.metadata or {}).get("username") or sa.handle
        password      = sa.credentials.get("password")
        session_file  = (sa.metadata or {}).get("session_file") or f"ig_session_{sa.id}.json"
        proxy         = (sa.metadata or {}).get("proxy")
        if not username or not password:
            return False, None, "missing instagram username or password"

        cl = _IG_CLIENTS.get(sa.id)
        if cl is None:
            cl = Client()
            cl.delay_range = [2, 5]
            if proxy:
                cl.set_proxy(proxy)

            session_path = Path(session_file)
            if session_path.exists():
                try:
                    cl.load_settings(session_path)
                    cl.login(username, password)
                    cl.get_timeline_feed()  # cheap session-validity check
                    log.info(f"[{automation.client_slug}] Instagram: reused session {session_path}")
                except LoginRequired:
                    log.warning(f"[{automation.client_slug}] Instagram: session expired, fresh login.")
                    cl = Client()
                    cl.delay_range = [2, 5]
                    if proxy:
                        cl.set_proxy(proxy)
                    cl.login(username, password)
                    cl.dump_settings(session_path)
            else:
                cl.login(username, password)
                cl.dump_settings(session_path)
                log.info(f"[{automation.client_slug}] Instagram: fresh login, saved {session_path}")

            _IG_CLIENTS[sa.id] = cl

        media_url = image_url or (automation.config.fallback_image_url if automation.config else None)
        if not media_url:
            return False, None, "no image_url and no fallback_image_url configured"

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img_data = httpx.get(media_url, timeout=30).content
            tmp.write(img_data)
            tmp_path = Path(tmp.name)

        media = cl.photo_upload(tmp_path, caption=text)
        tmp_path.unlink(missing_ok=True)

        log.info(f"[{automation.client_slug}] Posted to Instagram (unofficial, {getattr(media, 'pk', '?')}).")
        return True, str(getattr(media, "pk", "")), None
    except Exception as e:
        # Drop cached client so the next attempt re-validates.
        _IG_CLIENTS.pop(sa.id, None)
        return False, None, str(e)


POSTERS = {
    "twitter":   post_to_twitter,
    "linkedin":  post_to_linkedin,
    "instagram": post_to_instagram,
}


def _max_article_attempts_per_run() -> int:
    raw = (os.environ.get("MAX_ARTICLE_ATTEMPTS_PER_RUN") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 15))


def _ig_wait_then_publish_creation(
    account_id: str,
    creation_id: str,
    access_token: str,
    client_slug: str,
    *,
    poll_interval: float = 3.0,
    poll_timeout: float = 120.0,
    publish_retries: int = 6,
    publish_sleep: float = 4.0,
) -> tuple[bool, str | None, str | None]:
    """
    Instagram `/media` returns a container that is not instantly publishable.
    Poll `status_code` until FINISHED (or bail on ERROR). Then POST `media_publish`,
    retrying on Meta's transient 'not ready' (OAuth 9007 / subcode 2207027).
    """
    deadline = time.monotonic() + poll_timeout
    status_url = f"https://graph.facebook.com/v18.0/{creation_id}"
    while time.monotonic() < deadline:
        st = requests.get(status_url, params={
            "fields": "status_code",
            "access_token": access_token,
        }, timeout=30)
        if st.status_code != 200:
            return False, None, f"ig container status GET failed: {st.status_code} {st.text}"

        payload = st.json()
        status_code = payload.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            return False, None, (
                "Instagram container STATUS_ERROR "
                + str(payload.get("status") or payload)
            )

        log.info(
            f"[{client_slug}] Instagram container {creation_id} status={status_code!r}; "
            f"waiting {poll_interval:.0f}s",
        )
        time.sleep(poll_interval)
    else:
        return False, None, "Instagram container polling timed out without FINISHED"

    publish_url = f"https://graph.facebook.com/v18.0/{account_id}/media_publish"

    last_err: str | None = None
    for attempt in range(publish_retries):
        pr = requests.post(publish_url, params={
            "creation_id":  creation_id,
            "access_token": access_token,
        }, timeout=60)
        if pr.status_code == 200:
            body = pr.json()
            mid = body.get("id")
            log.info(f"[{client_slug}] Posted to Instagram (official, {mid}).")
            return True, str(mid) if mid else None, None

        last_err = pr.text or str(pr.status_code)
        transient = '"code":9007' in last_err or "Media ID is not available" in last_err or (
            '"error_subcode":2207027' in last_err
        )

        if transient and attempt < publish_retries - 1:
            log.info(
                f"[{client_slug}] Instagram publish not ready (attempt "
                f"{attempt + 1}/{publish_retries}), retry in {publish_sleep:.0f}s",
            )
            time.sleep(publish_sleep)
            continue

        break

    return False, None, f"{pr.status_code} Client Error: Bad Request — {last_err}"


# ─── Main posting job ────────────────────────────────────────────────────────

def run_posting_job(automation: Automation, trigger: str = "scheduled") -> None:
    """Run one full cycle for one automation."""
    log.info(f"─── [{automation.client_slug}] {automation.name} — running ───")

    run_id = create_run(automation.id, automation.client_id, trigger_source=trigger)

    try:
        articles = fetch_top_news(automation)
        if not articles:
            log.warning(f"[{automation.client_slug}] No articles available, skipping.")
            finalise_run(run_id, status="failed", articles_considered=0,
                         notes="no candidate articles after filters")
            return

        max_attempts = _max_article_attempts_per_run()
        candidates = articles[:max_attempts]
        summary_title = ""

        last_article_id: int | None = None
        for attempt_no, chosen in enumerate(candidates, start=1):
            article_id = upsert_article(
                automation_id=automation.id,
                client_id=automation.client_id,
                article=chosen,
                selection_status="chosen",
            )
            last_article_id = article_id
            summary_title = (chosen.get("title") or "")[:80]

            any_success = False
            any_failure = False

            for sa in automation.social_accounts:
                poster = POSTERS.get(sa.platform)
                if poster is None:
                    log.warning(f"[{automation.client_slug}] No poster for {sa.platform}, skipping.")
                    continue

                text = generate_post(chosen, sa.platform, automation)
                if text is None:
                    record_post(automation.id, automation.client_id, article_id,
                                sa.platform, "failed", "",
                                error_message="AI generation returned None")
                    any_failure = True
                    continue

                ok, external_id, err = poster(text, sa, automation, chosen.get("urlToImage"))

                record_post(
                    automation_id=automation.id,
                    client_id=automation.client_id,
                    article_id=article_id,
                    platform=sa.platform,
                    status="published" if ok else "failed",
                    generated_text=text,
                    external_id=external_id,
                    error_message=err,
                )

                if ok:
                    any_success = True
                else:
                    any_failure = True
                    log.warning(f"[{automation.client_slug}] {sa.platform} failed: {err}")

                time.sleep(2)  # gentle pacing between platforms

            if any_success and any_failure:
                status = "partial"
                finalise_run(run_id, status=status,
                             articles_considered=len(articles),
                             article_id_chosen=article_id)
                log.info(f"─── [{automation.client_slug}] Job complete ({status}) — "
                         f"{summary_title} ───\n")
                return

            if any_success and not any_failure:
                finalise_run(run_id, status="success",
                             articles_considered=len(articles),
                             article_id_chosen=article_id)
                log.info(f"─── [{automation.client_slug}] Job complete (success) — "
                         f"{summary_title} ───\n")
                return

            total_fail_all_platforms = not any_success
            remains = attempt_no < len(candidates)

            if total_fail_all_platforms and remains:
                log.warning(
                    f"[{automation.client_slug}] Article {attempt_no}/{len(candidates)} failed on "
                    f"every platform — trying next candidate.",
                )
                continue

            finalise_run(run_id, status="failed",
                         articles_considered=len(articles),
                         article_id_chosen=article_id or last_article_id)
            log.info(f"─── [{automation.client_slug}] Job complete (failed) — "
                     f"{summary_title} ───\n")
            return

    except Exception as e:
        log.exception(f"[{automation.client_slug}] Run crashed: {e}")
        finalise_run(run_id, status="failed", notes=str(e))
        db_log("error", f"Run crashed: {e}",
               run_id=run_id, automation_id=automation.id,
               client_id=automation.client_id)


# ─── Health check ────────────────────────────────────────────────────────────

def run_health_check(automations: list[Automation]) -> bool:
    log.info("════════════════════════════════════════")
    log.info("  HEALTH CHECK")
    log.info("════════════════════════════════════════")
    all_ok = True

    # NewsAPI
    try:
        NewsApiClient(api_key=NEWS_API_KEY).get_top_headlines(sources="bbc-news", page_size=1)
        log.info("  [ok]  NewsAPI")
    except Exception as e:
        log.error(f"  [FAIL] NewsAPI: {e}")
        all_ok = False

    # Anthropic
    try:
        anthropic.Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
            model=AI_MODEL, max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        log.info("  [ok]  Anthropic")
    except Exception as e:
        log.error(f"  [FAIL] Anthropic: {e}")
        all_ok = False

    # Per-automation summary
    if not automations:
        log.warning("  No active automations loaded from DB — nothing to schedule.")
        all_ok = False
    for a in automations:
        log.info(f"  [{a.client_slug}] {a.name}  tz={a.timezone}  dry_run={a.dry_run}  "
                 f"times={a.schedule_times}  platforms={[s.platform for s in a.social_accounts]}")

    log.info("════════════════════════════════════════\n")
    return all_ok


def _scheduler_poll_seconds() -> float:
    raw = (os.environ.get("SCHEDULER_POLL_SECONDS") or "10").strip()
    try:
        sec = float(raw)
    except ValueError:
        sec = 10.0
    return max(5.0, min(sec, 120.0))


# ─── Scheduler (timezone-aware, multi-automation) ────────────────────────────

def schedule_loop(*, type_filter: str = "news_posting") -> None:
    """
    Polling scheduler. On each tick (see SCHEDULER_POLL_SECONDS, default 10s),
    reloads active automations from Postgres so schedule_times, timezone, and
    credentials stay current without restarting the process. Then for each
    automation, if local time (that automation's timezone) matches a
    schedule_time and that slot has not fired today, runs the job.
    """
    poll = _scheduler_poll_seconds()
    last_fired: dict[tuple[int, str], str] = defaultdict(str)
    last_good: list[Automation] | None = None
    log.info(
        "Scheduler started (poll every %.0fs, reloads from DB each tick). Ctrl-C to stop.\n",
        poll,
    )

    while True:
        try:
            try:
                automations = load_active_automations(type_filter=type_filter)
                last_good = automations
            except Exception:
                log.exception("Failed to reload automations from DB")
                automations = last_good if last_good is not None else []
                if not automations:
                    log.warning("No automations in memory yet; waiting for a successful DB load.")

            for a in automations:
                try:
                    tz = ZoneInfo(a.timezone)
                except Exception:
                    log.error(f"[{a.client_slug}] Invalid timezone {a.timezone!r} — skipping.")
                    continue
                now_local = datetime.now(tz)
                current_hm = now_local.strftime("%H:%M")
                today = now_local.date().isoformat()
                for sched_time in a.schedule_times:
                    if current_hm == sched_time and last_fired[(a.id, sched_time)] != today:
                        last_fired[(a.id, sched_time)] = today
                        run_posting_job(a, trigger="scheduled")
            time.sleep(poll)
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user.")
            return
        except Exception as e:
            log.exception(f"Scheduler loop error: {e}")
            time.sleep(poll)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    automations = load_active_automations(type_filter="news_posting")
    ok = run_health_check(automations)

    if len(sys.argv) > 1 and sys.argv[1] == "--run-now":
        target_slug = sys.argv[2] if len(sys.argv) > 2 else None
        targets = [a for a in automations if (target_slug is None or a.client_slug == target_slug)]
        if not targets:
            log.error(f"No active news_posting automation found"
                      f"{f' for client {target_slug}' if target_slug else ''}.")
            sys.exit(1)
        for a in targets:
            run_posting_job(a, trigger="manual")
        return

    if not ok:
        log.warning("Health check failed; scheduler starting anyway.")
    schedule_loop(type_filter="news_posting")


if __name__ == "__main__":
    main()
