"""
News Automation Script
======================
Fetches top world news articles, generates AI-powered neutral summaries,
and posts to X (Twitter), LinkedIn, and Instagram on a schedule.

Author: Luke Carter — AI Automation Specialist
"""

import os
import json
import time
import random
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import schedule
import requests
from newsapi import NewsApiClient
import anthropic
import tweepy

# ─── Setup ───────────────────────────────────────────────────────────────────

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("automation.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

# News API (https://newsapi.org)
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

# Anthropic (https://console.anthropic.com)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Twitter/X (https://developer.twitter.com)
TWITTER_API_KEY        = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET     = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN   = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET  = os.getenv("TWITTER_ACCESS_SECRET")
TWITTER_BEARER_TOKEN   = os.getenv("TWITTER_BEARER_TOKEN")

# LinkedIn (https://www.linkedin.com/developers)
LINKEDIN_ACCESS_TOKEN  = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN    = os.getenv("LINKEDIN_PERSON_URN")  # e.g. "urn:li:person:ABC123"

# Instagram (Meta Graph API — https://developers.facebook.com)
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID   = os.getenv("INSTAGRAM_ACCOUNT_ID")

# Posting config
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # Set to true to test without posting
POSTS_PER_DAY = 3

# ─── Persistent Deduplication ────────────────────────────────────────────────
# Tracks posted article URLs across restarts so we never repost the same article

POSTED_URLS_FILE = Path("posted_urls.json")

def load_posted_urls() -> set:
    if POSTED_URLS_FILE.exists():
        try:
            return set(json.loads(POSTED_URLS_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_posted_url(url: str):
    urls = load_posted_urls()
    urls.add(url)
    # Keep last 500 URLs to prevent file growing indefinitely
    trimmed = list(urls)[-500:]
    POSTED_URLS_FILE.write_text(json.dumps(trimmed))

ALREADY_POSTED = load_posted_urls()


def is_configured(*keys: str) -> bool:
    """Returns True only if all given env keys are set and not placeholder values."""
    return all(
        os.getenv(k) and not os.getenv(k, "").startswith("your_")
        for k in keys
    )


# ─── News Fetcher ─────────────────────────────────────────────────────────────

def fetch_top_news(max_articles: int = 10) -> list[dict]:
    """
    Fetches top world news from neutral, internationally-focused sources only.
    Sources: Reuters, AP, BBC News, Al Jazeera, France 24 — no domestic or entertainment outlets.
    """
    newsapi = NewsApiClient(api_key=NEWS_API_KEY)

    # Neutral, internationally-recognised sources only
    TRUSTED_SOURCES = "reuters,associated-press,bbc-news,al-jazeera-english,france-24"

    # Keywords that indicate entertainment, celebrity, or non-global content — skip these
    EXCLUDE_KEYWORDS = [
        "nfl", "nba", "celebrity", "box office", "kardashian", "oscars",
        "republican", "democrat", "gop", "trump", "biden", "maga",
        "reality tv", "breakfast show", "radio show", "tv show", "presenter",
        "replaces", "sitcom", "album", "tour", "singer", "actor", "actress",
        "film review", "movie review", "box office", "grammy", "bafta",
        "premier league", "champions league", "formula 1", "grand prix",
        "cricket", "rugby", "tennis", "golf"
    ]

    # At least one of these must appear for the article to qualify as world/global news
    REQUIRE_KEYWORDS = [
        "global", "international", "world", "united nations", "un ", "nato",
        "war", "conflict", "peace", "climate", "summit", "treaty", "sanctions",
        "crisis", "government", "president", "prime minister", "election",
        "economy", "trade", "migration", "humanitarian", "nuclear", "diplomacy",
        "protest", "military", "aid", "ceasefire", "refugee", "poverty"
    ]

    try:
        response = newsapi.get_top_headlines(
            sources=TRUSTED_SOURCES,
            language="en",
            page_size=max_articles * 2  # Fetch extra to account for filtering
        )
        articles = response.get("articles", [])

        filtered = []
        for a in articles:
            title = a.get("title", "")
            description = a.get("description", "")
            combined = (title + " " + description).lower()

            # Skip if missing key fields or already posted
            if not (title and a.get("description") and a.get("url")):
                continue
            if title == "[Removed]" or a["url"] in ALREADY_POSTED:
                continue
            # Skip if matches any excluded keywords
            if any(kw in combined for kw in EXCLUDE_KEYWORDS):
                continue
            # Skip if it doesn't contain at least one world/global news keyword
            if not any(kw in combined for kw in REQUIRE_KEYWORDS):
                continue

            filtered.append(a)

        result = filtered[:max_articles]
        log.info(f"Fetched {len(result)} world news articles from trusted sources.")
        return result

    except Exception as e:
        log.error(f"Failed to fetch news: {e}")
        return []


# ─── AI Summariser ────────────────────────────────────────────────────────────

def generate_post(article: dict, platform: str) -> str:
    """
    Uses Claude to generate a neutral, platform-appropriate post for the given article.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    title       = article.get("title", "")
    description = article.get("description", "")
    source      = article.get("source", {}).get("name", "Unknown source")
    url         = article.get("url", "")

    platform_instructions = {
        "twitter": (
            "Write a concise, neutral tweet (max 240 characters). "
            "No hashtags unless they fit naturally. End with the article URL. "
            "Do not editorialize — present the facts only."
        ),
        "linkedin": (
            "Write a professional LinkedIn post (2-3 short paragraphs). "
            "Neutral and factual tone. Include a brief one-sentence summary, "
            "a key detail or stat from the description, and end with the article URL. "
            "No clickbait or emotional language."
        ),
        "instagram": (
            "Write an Instagram caption (2-4 sentences). "
            "Neutral and informative. Add 5-8 relevant hashtags at the end (e.g. #WorldNews #GlobalAffairs). "
            "End with the article URL."
        ),
    }

    instructions = platform_instructions.get(platform, platform_instructions["twitter"])

    prompt = f"""You are a neutral world news summariser. Your job is to create social media posts about international news and global issues — without bias, opinion, partisan framing, or sensationalism. Do not take sides. Report facts only. Avoid emotionally charged language.

Article title: {title}
Article description: {description}
Source: {source}
URL: {url}

{instructions}

Return only the post text. No preamble, no explanation."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        post_text = message.content[0].text.strip()
        log.info(f"Generated {platform} post for: {title[:60]}...\n{'-'*60}\n{post_text}\n{'-'*60}")
        return post_text

    except Exception as e:
        log.error(f"Failed to generate post for {platform}: {e}")
        return None


# ─── Posters ──────────────────────────────────────────────────────────────────

def post_to_twitter(text: str) -> bool:
    """Posts a tweet using the Twitter/X API v2 via Tweepy."""
    if DRY_RUN:
        log.info(f"[DRY RUN] Twitter post:\n{text}\n")
        return True

    try:
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        response = client.create_tweet(text=text)
        log.info(f"✅ Posted to Twitter. Tweet ID: {response.data['id']}")
        return True

    except tweepy.errors.TweepyException as e:
        log.error(f"Twitter post failed: {e}")
        return False


def post_to_linkedin(text: str) -> bool:
    """Posts to LinkedIn using the REST API."""
    if DRY_RUN:
        log.info(f"[DRY RUN] LinkedIn post:\n{text}\n")
        return True

    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    payload = {
        "author": LINKEDIN_PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        log.info(f"✅ Posted to LinkedIn.")
        return True

    except requests.HTTPError as e:
        log.error(f"LinkedIn post failed: {e} — {response.text}")
        return False


def post_to_instagram(text: str, image_url: str = None) -> bool:
    """
    Posts to Instagram via the Meta Graph API.
    Requires a public image URL — Instagram does not support text-only posts.
    Falls back to a default news graphic if no image is provided.
    """
    if DRY_RUN:
        log.info(f"[DRY RUN] Instagram post:\n{text}\n")
        return True

    # Instagram requires an image — use article image or a default placeholder
    media_url = image_url or "https://via.placeholder.com/1080x1080.png?text=World+News"

    # Step 1: Create media container
    create_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/media"
    create_params = {
        "image_url": media_url,
        "caption": text,
        "access_token": INSTAGRAM_ACCESS_TOKEN
    }

    try:
        create_response = requests.post(create_url, params=create_params)
        create_response.raise_for_status()
        creation_id = create_response.json().get("id")

        if not creation_id:
            log.error("Instagram: No creation ID returned.")
            return False

        # Step 2: Publish the container
        publish_url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/media_publish"
        publish_params = {
            "creation_id": creation_id,
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        publish_response = requests.post(publish_url, params=publish_params)
        publish_response.raise_for_status()

        log.info(f"✅ Posted to Instagram.")
        return True

    except requests.HTTPError as e:
        log.error(f"Instagram post failed: {e}")
        return False


# ─── Main Job ─────────────────────────────────────────────────────────────────

def run_posting_job():
    """
    The main job: fetch one article, generate posts for each platform, and publish.
    """
    log.info("─── Running posting job ───")

    articles = fetch_top_news(max_articles=15)
    if not articles:
        log.warning("No articles available. Skipping this run.")
        return

    # Pick a random unused article
    article = random.choice(articles)
    ALREADY_POSTED.add(article["url"])
    save_posted_url(article["url"])

    article_image = article.get("urlToImage")

    # Only include platforms whose credentials are fully configured
    all_platforms = {
        "twitter":   (post_to_twitter,   ["TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET"]),
        "linkedin":  (post_to_linkedin,  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"]),
        "instagram": (lambda text: post_to_instagram(text, image_url=article_image), ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"]),
    }

    platforms = {
        name: fn for name, (fn, keys) in all_platforms.items()
        if is_configured(*keys)
    }

    skipped = set(all_platforms.keys()) - set(platforms.keys())
    if skipped:
        log.info(f"Skipping unconfigured platforms: {', '.join(skipped)}")

    for platform, post_fn in platforms.items():
        post_text = generate_post(article, platform)
        if post_text:
            success = post_fn(post_text)
            if not success:
                log.warning(f"Failed to post to {platform} — will retry next cycle.")
        time.sleep(2)  # Brief pause between platform posts

    log.info(f"─── Job complete. Article: {article['title'][:80]} ───\n")


# ─── Health Check ─────────────────────────────────────────────────────────────

def run_health_check():
    """
    Verifies all configured API connections are live before the scheduler starts.
    Prints a clear pass/fail for each platform.
    """
    log.info("════════════════════════════════════════")
    log.info("  HEALTH CHECK — verifying connections")
    log.info("════════════════════════════════════════")
    all_ok = True

    # NewsAPI
    try:
        newsapi = NewsApiClient(api_key=NEWS_API_KEY)
        result = newsapi.get_top_headlines(sources="bbc-news", page_size=1)
        if result.get("articles"):
            log.info("  ✅  NewsAPI          — connected")
        else:
            raise ValueError("No articles returned")
    except Exception as e:
        log.error(f"  ❌  NewsAPI          — FAILED: {e}")
        all_ok = False

    # Anthropic
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}]
        )
        log.info("  ✅  Anthropic (Claude) — connected")
    except Exception as e:
        log.error(f"  ❌  Anthropic (Claude) — FAILED: {e}")
        all_ok = False

    # Twitter
    if is_configured("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET"):
        try:
            client = tweepy.Client(
                consumer_key=TWITTER_API_KEY,
                consumer_secret=TWITTER_API_SECRET,
                access_token=TWITTER_ACCESS_TOKEN,
                access_token_secret=TWITTER_ACCESS_SECRET,
            )
            client.get_me()
            log.info("  ✅  Twitter/X        — connected")
        except Exception as e:
            log.error(f"  ❌  Twitter/X        — FAILED: {e}")
            all_ok = False
    else:
        log.info("  ⏭️   Twitter/X        — skipped (not configured)")

    # LinkedIn
    if is_configured("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"):
        try:
            response = requests.get(
                "https://api.linkedin.com/v2/me",
                headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"}
            )
            response.raise_for_status()
            name = response.json().get("localizedFirstName", "unknown")
            log.info(f"  ✅  LinkedIn         — connected (as {name})")
        except Exception as e:
            log.error(f"  ❌  LinkedIn         — FAILED: {e}")
            all_ok = False
    else:
        log.info("  ⏭️   LinkedIn         — skipped (not configured)")

    # Instagram
    if is_configured("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"):
        try:
            response = requests.get(
                f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}",
                params={"fields": "username", "access_token": INSTAGRAM_ACCESS_TOKEN}
            )
            response.raise_for_status()
            username = response.json().get("username", "unknown")
            log.info(f"  ✅  Instagram        — connected (@{username})")
        except Exception as e:
            log.error(f"  ❌  Instagram        — FAILED: {e}")
            all_ok = False
    else:
        log.info("  ⏭️   Instagram        — skipped (not configured)")

    log.info("════════════════════════════════════════")
    if all_ok:
        log.info("  All systems operational. Starting scheduler.")
    else:
        log.warning("  One or more connections failed. Check errors above.")
    log.info("════════════════════════════════════════\n")
    return all_ok


# ─── Scheduler ────────────────────────────────────────────────────────────────

def start_scheduler():
    """
    Runs a health check, then schedules the posting job 3 times per day:
    8am, 1pm, and 6pm (local time).
    """
    run_health_check()

    schedule.every().day.at("08:00").do(run_posting_job)
    schedule.every().day.at("13:00").do(run_posting_job)
    schedule.every().day.at("18:00").do(run_posting_job)

    log.info(f"Scheduler active. Next posts at 08:00, 13:00, 18:00 daily.")
    log.info(f"DRY_RUN mode: {'ON ⚠️' if DRY_RUN else 'OFF — posts are live'}\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--run-now":
        # Useful for testing: run one job immediately
        log.info("Running one posting job immediately (--run-now flag detected).")
        run_posting_job()
    else:
        start_scheduler()
