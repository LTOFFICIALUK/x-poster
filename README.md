# Social Media Automation System
### Automated world news posting — neutral, scheduled, and hands-free

---

## What It Does

This system monitors trusted international news sources around the clock, selects relevant world news and global affairs stories, rewrites them into neutral platform-appropriate posts using AI, and publishes them automatically — three times a day, every day, without any manual input.

Once set up, it runs entirely on its own.

---

## At a Glance

| | |
|---|---|
| **Posts per day** | 3 (08:00 · 13:00 · 18:00) |
| **Platforms** | X (Twitter) · LinkedIn · Instagram |
| **News sources** | Reuters · AP · BBC News · Al Jazeera · France 24 |
| **Content type** | Non-biased world politics & global issues |
| **AI model** | Claude (Anthropic) |
| **Language** | Python 3.10+ |

---

## How It Works

```
Trusted news sources  →  Filter for world/global content  →  AI generates neutral post
        ↓
Post to X · LinkedIn · Instagram  →  Log result  →  Wait for next scheduled run
```

1. **Fetch** — pulls the latest headlines from a curated list of internationally recognised, politically neutral news outlets
2. **Filter** — removes entertainment, sports, and domestic political content; requires articles to contain genuine global news signals
3. **Summarise** — Claude AI rewrites each article into a concise, factual, non-partisan post tailored to each platform's format and tone
4. **Post** — publishes to all configured platforms with a brief pause between each
5. **Log** — records every action, success, and failure to `automation.log`
6. **Deduplicate** — remembers every article it has posted (persisted across restarts) so the same story is never posted twice

---

## Platforms

| Platform | Status | Notes |
|---|---|---|
| X (Twitter) | ✅ Supported | Requires X Developer account (pay-per-use credits) |
| LinkedIn | ✅ Supported | Free via LinkedIn Developer API |
| Instagram | ✅ Supported | Requires Meta Business account + Graph API |

Platforms are skipped cleanly if credentials are not configured — the system will post to whichever platforms are set up and log a clear message for the rest.

---

## Requirements

- Python 3.10 or newer
- API keys for: [NewsAPI](https://newsapi.org) · [Anthropic](https://console.anthropic.com) · and whichever social platforms you want active
- See `News_Automation_Setup_Guide.docx` for a full step-by-step walkthrough

Install dependencies:
```bash
pip install -r requirements.txt
```

---

## Quick Start

**1. Configure your keys**
```bash
cp .env.template .env
# Open .env and fill in your API credentials
```

**2. Test without posting (dry run)**
```bash
DRY_RUN=true python3 news_automation.py --run-now
```

**3. Run one live post**
```bash
python3 news_automation.py --run-now
```

**4. Start the scheduler**
```bash
python3 news_automation.py
```

On startup, the system runs a health check and confirms every connection before the scheduler begins:

```
════════════════════════════════════════
  HEALTH CHECK — verifying connections
════════════════════════════════════════
  ✅  NewsAPI           — connected
  ✅  Anthropic (Claude) — connected
  ✅  Twitter/X         — connected
  ✅  LinkedIn          — connected (as Luke)
  ⏭️   Instagram         — skipped (not configured)
════════════════════════════════════════
  All systems operational. Starting scheduler.
════════════════════════════════════════
```

---

## Configuration

All configuration is handled through environment variables in your `.env` file. A full template with descriptions is provided in `.env.template`.

| Variable | Required | Description |
|---|---|---|
| `NEWS_API_KEY` | ✅ | NewsAPI key — newsapi.org |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key — console.anthropic.com |
| `TWITTER_API_KEY` | Optional | X Developer app key |
| `TWITTER_API_SECRET` | Optional | X Developer app secret |
| `TWITTER_ACCESS_TOKEN` | Optional | X access token (Read & Write) |
| `TWITTER_ACCESS_SECRET` | Optional | X access token secret |
| `LINKEDIN_ACCESS_TOKEN` | Optional | LinkedIn OAuth 2.0 token |
| `LINKEDIN_PERSON_URN` | Optional | LinkedIn member URN |
| `INSTAGRAM_ACCESS_TOKEN` | Optional | Meta Graph API token |
| `INSTAGRAM_ACCOUNT_ID` | Optional | Instagram Business account ID |
| `DRY_RUN` | Optional | Set to `true` to test without posting |

---

## Project Structure

```
Social Media Automation/
├── news_automation.py          # Main script
├── .env.template               # API key template
├── requirements.txt            # Python dependencies
├── News_Automation_Setup_Guide.docx  # Full setup guide
├── automation.log              # Runtime log (auto-created)
└── posted_urls.json            # Deduplication store (auto-created)
```

---

## Built By

**Teventis** — AI automation systems for fitness & leisure businesses  
Luke Carter · AI Automation Specialist  
[teventis.com](https://teventis.com)
