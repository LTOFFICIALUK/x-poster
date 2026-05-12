# Client Setup Questionnaire
### Social Media Automation — News Posting System
*Complete this before any technical work begins. Every question has a direct impact on the setup.*

---

## 1. Business Information

| Question | Client Answer |
|---|---|
| Business name | |
| Industry / niche | |
| Target audience (age, location, interests) | |
| Primary goal (brand awareness, lead gen, engagement) | |
| Preferred tone (formal, conversational, neutral) | |

---

## 2. Content Preferences

These determine what news gets pulled and what gets filtered out.

**News category** — choose one primary:

| Category | Description |
|---|---|
| `general` | Top world headlines — broadest mix |
| `business` | Markets, economy, corporate news |
| `technology` | Tech industry, innovation, AI |
| `health` | Medical research, public health, wellbeing |
| `science` | Scientific discoveries, environment, space |
| `entertainment` | Film, music, culture (not recommended for B2B) |
| `sports` | Sport news and results |

> **Selected category:** _______________

**Keywords to always include** (posts must contain at least one):
> e.g. "climate, sustainability, global trade"
> _______________

**Keywords to always exclude** (posts containing these are skipped):
> e.g. competitor names, sensitive topics, specific politicians
> _______________

**Posting frequency:**
- [ ] 1x per day
- [ ] 2x per day
- [ ] 3x per day *(default)*
- [ ] Other: _______________

**Preferred posting times** (local timezone):
> _______________  /  _______________  /  _______________

**Client timezone:**
> _______________

---

## 3. Platforms

For each platform, confirm whether the client wants it active and collect the required credentials.

---

### Instagram

**Active?** Yes / No

**Does the client have an existing Instagram account?**
- [ ] Yes — username: _______________
- [ ] No — we need to create one

**Account type:**
- [ ] Already a Business/Professional account
- [ ] Personal account — needs switching to Business
- [ ] Not sure

**Facebook Page linked?**
- [ ] Yes — page name: _______________
- [ ] No

> **Why is a Facebook Page required?**
> Meta's Instagram Graph API only works with Instagram Professional (Business/Creator) accounts. A Facebook Page is a structural requirement — Meta's permission system links your Instagram account *through* a Page to grant API access. The permission chain is: **Facebook User → Facebook Page (admin) → Linked Instagram Professional Account → API Access**. Without this link, the API cannot surface your Instagram account during the OAuth flow.
>
> **The Page does not need to be a real brand or public-facing page.** An old side project, a test page, or a blank page you create just for this purpose all work equally well. Meta does not validate followers, posts, or activity. The only requirements are: (1) you have **admin role** on the Page, and (2) the Page is **connected to the Instagram account** via Instagram Settings → Linked Accounts or Meta Business Suite.

**Instagram API mode:**

| Mode | When to use | What we need |
|---|---|---|
| **Official** (recommended) | Established accounts with an existing Facebook Page | Access Token + Account ID |
| **Unofficial** | New accounts, no Facebook Page, proof-of-concept | Username + Password |

> **Selected mode:** _______________

*Official mode credentials (if applicable):*
| Key | Value |
|---|---|
| `INSTAGRAM_ACCESS_TOKEN` | |
| `INSTAGRAM_ACCOUNT_ID` | |

*Unofficial mode credentials (if applicable):*
| Key | Value |
|---|---|
| `INSTAGRAM_USERNAME` | |
| `INSTAGRAM_PASSWORD` | |

---

### LinkedIn

**Active?** Yes / No

**Does the client have a LinkedIn Company Page?**
- [ ] Yes — page URL: _______________
- [ ] No — needs creating before setup

**Does the client have a LinkedIn Developer App?**
- [ ] Yes
- [ ] No — we will create one

*Credentials required:*
| Key | Value |
|---|---|
| `LINKEDIN_ACCESS_TOKEN` | |
| `LINKEDIN_PERSON_URN` | |

> ⚠️ LinkedIn access tokens expire every 60 days. Schedule a token refresh reminder at setup.

---

### X (Twitter)

**Active?** Yes / No

**Does the client have an X Developer account?**
- [ ] Yes
- [ ] No — needs applying for at developer.twitter.com

**X API credits purchased?**
- [ ] Yes
- [ ] No — required for posting (pay-per-use)

*Credentials required:*
| Key | Value |
|---|---|
| `TWITTER_API_KEY` | |
| `TWITTER_API_SECRET` | |
| `TWITTER_ACCESS_TOKEN` | |
| `TWITTER_ACCESS_SECRET` | |
| `TWITTER_BEARER_TOKEN` | |

> ⚠️ X API requires Read & Write permissions. Regenerate access tokens after changing permissions or they will return 401 errors.

---

### TikTok

**Active?** Yes / No

> ⚠️ TikTok's official API is video-only and requires formal approval. For text/image news posts, we recommend using **Buffer** as an interim scheduler and feeding it the AI-generated content manually until a video automation workflow is in place.

- [ ] Client happy to use Buffer for TikTok in the interim
- [ ] Client wants to wait for video automation
- [ ] Not a priority

---

## 4. Hosting

Where will the automation run? It must be always-on — it cannot run from a personal laptop.

| Option | Cost | Best for |
|---|---|---|
| **Railway** | Free tier available | Quick setup, recommended for most clients |
| **Render** | Free tier available | Alternative to Railway |
| **DigitalOcean** | ~£4/month | Clients who want dedicated infrastructure |
| **Client's own server** | Varies | Enterprise clients |

> **Selected hosting option:** _______________

---

## 5. Sign-off Checklist

Before going live, confirm all of the following with the client:

- [ ] Content category and keyword preferences confirmed
- [ ] All active platforms have valid credentials in `.env`
- [ ] Dry run completed — client has reviewed and approved sample post output
- [ ] Posting times confirmed and set in scheduler
- [ ] Script deployed to server and health check passed (all green ticks)
- [ ] Client informed that LinkedIn tokens expire every 60 days and need refreshing
- [ ] Client briefed on Instagram mode chosen and any associated risks (if unofficial)
- [ ] Log monitoring confirmed — client knows how to check `automation.log`

---

*Document prepared by Teventis — AI Automation Specialist: Luke Carter*
