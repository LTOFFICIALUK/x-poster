# PRICING.md — News Automation Service

Internal cost reference for the automated news-posting system. Use this to size client tiers and set prices. All figures verified against official sources on 24 April 2026 — see the Sources section at the bottom. Where a number is an estimate or assumption, it is explicitly marked.

---

## TL;DR

The single biggest cost driver is **X (Twitter) API posts that contain a URL** — and our news automation posts always include a source link. That charge is **$0.20 per post** under the rules that took effect on 20 April 2026 (four days ago).

At three posts per day (our current default cadence), that is **$18/client/month** for X alone. LinkedIn, Instagram and TikTok add no per-post cost today. Claude Haiku LLM cost is negligible (~$2.50/client/month). NewsAPI's free tier is not licensed for commercial use, so the real floor for a paying product is the NewsAPI Business plan at **$449/month**, amortised across all clients.

Note: LLM cost is an over-estimation.

---

## 1. X (Twitter) API — pay-per-use

### 1.1 Pricing model

On 6 February 2026, X replaced its tiered plans (Free / Basic / Pro) with a pay-per-use model for all new developers. No free tier is available to new sign-ups. No subscription or minimum spend. Credits are purchased upfront in the X Developer Console and deducted per API call. Legacy Free-tier users were transitioned with a one-time $10 voucher.

### 1.2 Rates (effective 20 April 2026)

| Action                                                 | Cost per call     |
| ------------------------------------------------------ | ----------------- |
| Create post — **text only**                            | **$0.015**        |
| Create post — **containing any URL**                   | **$0.20**         |
| Create post — summoned reply (even if it has a URL)    | $0.01             |
| Read post                                              | $0.005            |
| User lookup                                            | $0.01             |
| Owned reads (your own posts, followers, likes, lists)  | $0.001            |

Additional rules to note: Following, liking and quote-posting via the API write endpoints have been removed from self-serve tiers. Pay-per-use is capped at 2 million post reads per month; Enterprise is required above that. Rebate: once cumulative spend reaches $200 in a cycle, 10% of spend comes back as xAI credits; 15% at $500; 20% at $1,000.

### 1.3 What this means for us

**Every post our system publishes contains a source URL.** That puts us on the $0.20 rate for every X post, not the $0.015 text-only rate. Cost scales linearly with post volume:

| Cadence      | Posts / month | X cost / client / month |
| ------------ | ------------- | ----------------------- |
| 2 / day      | 60            | $12.00                  |
| 3 / day      | 90            | $18.00                  |
| 5 / day      | 150           | $30.00                  |
| 10 / day     | 300           | $60.00                  |

### 1.4 Cost-reduction options worth testing

These are ideas, not verified savings — each needs a test against a staging account before we quote numbers that assume them.

The first option is to publish the post as text-only at $0.015 and add the source link as a reply. The "summoned reply" $0.01 rate only applies to a specific mechanic, and I have not been able to confirm whether a normal self-reply with a URL is priced at $0.015 or $0.20 — this needs verifying in the Developer Console before we rely on it.

The second option is to hit the $200 rebate threshold deliberately by batching multiple clients' credits into a single company-wide account and using separate OAuth tokens per client. At $0.20 × 90 = $18/client, we hit $200 at about 11 clients — above that we start clawing back 10-20% in xAI credits.

---

## 2. NewsAPI — article source

### 2.1 Plans

| Plan        | Monthly cost | Requests     | Commercial use allowed? |
| ----------- | ------------ | ------------ | ----------------------- |
| Developer   | Free         | 100 / day    | **No** — dev/test only  |
| Business    | $449         | 250,000 / mo | Yes                     |
| Enterprise  | ~$1,749      | Higher       | Yes                     |

### 2.2 What this means for us

The free plan we're currently using is explicitly restricted by NewsAPI's terms to development and testing only — it cannot be used in staging or production. The moment we onboard a paying client, we need to move to the Business plan at $449/month or swap providers.

One Business subscription at 250,000 requests/month easily covers our volume (3 articles/day × 100 clients = 9,000 requests/month), so the per-client amortised cost drops fast with scale:

| Clients | NewsAPI cost / client / month |
| ------- | ----------------------------- |
| 1       | $449.00                       |
| 10      | $44.90                        |
| 25      | $17.96                        |
| 50      | $8.98                         |
| 100     | $4.49                         |

Worth flagging to Rio: alternative news APIs (Currents, Newsdata.io, GNews) have cheaper commercial tiers. If we stay small for a while, swapping providers could save meaningful money. I have not fully priced these alternatives here — that's a follow-up.

---

## 3. Claude LLM — neutral summary generation

Using Claude Haiku 4.5 (the cheapest capable model for this task):

- Input: $1.00 / million tokens
- Output: $5.00 / million tokens
- Prompt caching: up to 90% savings on repeat context
- Batch API: 50% discount if latency doesn't matter

Estimated tokens per summary (my assumption, worth measuring against real runs): ~1,000 input tokens (article passed in as context) + ~300 output tokens (neutral summary). That works out to roughly **$0.0025 per post**, or about **$0.23/client/month** at 3 posts/day. Negligible next to X.

---

## 4. LinkedIn, Instagram, TikTok

No direct per-post API cost at the free developer tier for any of these three today. Each has its own friction, though, and their terms change — I'd recommend we re-verify each before quoting an enterprise client.

LinkedIn's Marketing/Community APIs are free; rate-limited; certain scopes require app review. Instagram posting uses the Meta Graph API, which is free but requires a Facebook Developer account and an Instagram Business or Creator account, plus Meta app review for publishing permissions. TikTok's Content Posting API is free but requires approval into the Developer Program.

**Assumption flag:** I'm treating these as $0/post for now. If any of them moves to a paid model, the tier structure below will need revisiting.

---

## 5. Per-client cost summary

Variable cost per client, by cadence, assuming every post contains a URL:

| Cadence  | X API  | LLM    | Total variable | + NewsAPI share @ 10 clients | + NewsAPI share @ 50 clients |
| -------- | ------ | ------ | -------------- | ---------------------------- | ---------------------------- |
| 2 / day  | $12.00 | $0.15  | **$12.15**     | $57.05                       | $21.13                       |
| 3 / day  | $18.00 | $0.23  | **$18.23**     | $63.13                       | $27.21                       |
| 5 / day  | $30.00 | $0.38  | **$30.38**     | $75.28                       | $39.36                       |
| 10 / day | $60.00 | $0.75  | **$60.75**     | $105.65                      | $69.73                       |

Fixed costs not shown above: NewsAPI Business plan ($449/mo, or nothing if we stay dev-only and don't monetise). Any hosting, logging, error-monitoring we end up using.

---

## 6. Proposed client tier structure

Suggested starting point for Rio — margins target ~70%+ at a 10-client book of business. All prices in GBP since our clients are UK-based; cost inputs are USD so these assume ~1.27 USD/GBP and round up for a safety buffer. Revisit if FX moves.

| Tier        | Included platforms              | Post cadence  | Our cost (10 clients, USD) | Suggested price (GBP/mo) | Approx. margin |
| ----------- | ------------------------------- | ------------- | -------------------------- | ------------------------ | -------------- |
| Starter     | X only                          | 2 / day       | ~$57                       | £149                     | ~70%           |
| Standard    | X + LinkedIn                    | 3 / day       | ~$63                       | £199                     | ~75%           |
| Pro         | X + LinkedIn + Instagram        | 5 / day       | ~$75                       | £299                     | ~80%           |
| Enterprise  | All four + TikTok, custom cadence | 10+ / day   | ~$106+                     | Custom (≥ £599)          | —              |

These are first-draft numbers to argue with, not commitments. Key levers: NewsAPI amortisation improves as we onboard more clients; X's $0.20 URL rate could drop if we find a working reply-link workaround; we get 10-20% xAI rebate above $200/month spend.

---

## 7. Risks and unknowns to watch

The X API pricing is **four days old** (effective 20 April 2026). The 20× hike on URL posts is explicitly framed by X as a spam/abuse measure; it may move again. We should not sign multi-month fixed-price contracts with clients without a pass-through clause for X API rate changes.

The "summoned reply at $0.01" exception is the single biggest potential saving (15× cheaper than a URL post) but I have not been able to confirm from public docs what exactly qualifies as a summoned reply. Before we build tooling around it, someone should test it against a staging X account and read the rate in the Developer Console directly.

Media upload costs for images/video attached to tweets are **not documented publicly** — rates are visible in the Developer Console only. If we plan to post images, we need to pull those rates and re-run the model.

NewsAPI terms explicitly restrict the free tier to dev/test. Using it in production for paying clients would be a breach — we need the Business plan or a different provider before day one of our first paid engagement.

LinkedIn, Instagram and TikTok are treated here as free-to-post, which is true for the base API today but each requires app-review friction and none guarantees that position going forward.

---

## Sources

- [X API Pricing Update: Owned Reads Now $0.001 + Other Changes Effective April 20, 2026 (X Developers official)](https://devcommunity.x.com/t/x-api-pricing-update-owned-reads-now-0-001-other-changes-effective-april-20-2026/263025)
- [Announcing the Launch of X API Pay-Per-Use Pricing (X Developers official)](https://devcommunity.x.com/t/announcing-the-launch-of-x-api-pay-per-use-pricing/256476)
- [X API pricing documentation](https://docs.x.com/x-api/getting-started/pricing)
- [TechCrunch — X makes it more expensive to post links through its API (22 April 2026)](https://techcrunch.com/2026/04/22/x-makes-it-more-expensive-to-post-links-through-its-api/)
- [X API Pricing is Now Only Usage Based, No Fixed Plans — PriceTimeline](https://pricetimeline.com/news/204)
- [NewsAPI.org pricing page](https://newsapi.org/pricing)
- [NewsAPI.org terms of service](https://newsapi.org/terms)
- [Anthropic Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Haiku 4.5 product page](https://www.anthropic.com/claude/haiku)

_Last verified: 24 April 2026._
