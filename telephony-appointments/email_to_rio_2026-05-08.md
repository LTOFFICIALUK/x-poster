# Friday Rundown — Email Draft for Rio

**Send:** Friday evening, 2026-05-08
**To:** Rio Sanderson
**Subject:** Re: Upcoming Ad Campaign Preparation

---

Hi Rio,

As promised, here's the Friday rundown on the AI appointment setter. Kept it tight — let me know what you want to dig into and I'll come back with detail.

**What's been built so far**

This week was about laying the foundations so we're not improvising once the accounts are wired up.

- Full requirements doc covering scope, the inbound flow, qualification questions per service, edge cases, persona, recording disclosure, SMS confirmation copy, retention, and the test plan. Working document — happy to share if you want to see exactly what Riley will and won't do.
- Riley's persona and opening line drafted (female voice, calm, professional, recording disclosure built into the first 7-8 seconds).
- Light qualification questions written for each of the five services so Riley captures enough context for you to prep without turning the call into an audit.
- Decided we plug into the existing stack rather than spin up parallel infrastructure — code lives in `teventis-automations/telephony-appointments`, database tables added via the existing `teventis-database` migration pattern, and we'll add a "Call History" view to the admin panel so you can listen back to recordings and read transcripts in one place.

**How we're moving forward**

Sticking to the plan you signed off on:

- Flow A only for v1 (caller dials in → Riley qualifies → books into your Calendly → SMS confirms). Flow B (Riley calls you first) is built with hooks but not switched on until A is stable in production.
- Vapi for telephony, STT, TTS, plus a UK number provisioned through them — saves us juggling Twilio.
- Claude as the brain.
- Calendly API hitting your discovery-call link for live availability and booking.
- Recording on, with disclosure in the opening line. 30-day retention on audio, 12 months on transcripts.
- Edge cases (caller is outside UK, existing client with a support issue, anything Riley can't confidently handle) → Riley offers *"let me get Rio to call you back directly, would that work?"* — captures details, SMS to your mobile, no Calendly slot booked. Defaults to keeping the lead warm rather than bouncing them.
- Ads launch only once we're complete here, so I'm sequencing ICO registration and the final privacy policy review into the soft-launch window (privacy draft is already in the repo ready for solicitor review when we get there).

**What I need from you — sooner the better**

Three things genuinely block progress, plus one I'd love to lock in:

1. **Set up a Vapi account** (vapi.ai) and send me the login + API key. We'll use it to provision the UK number and build Riley.
2. **Calendly Personal Access Token** — generated from your Calendly account settings (Integrations → API & Webhooks). Read + write access.
3. **Your mobile number** — for callback escalations from Riley today (existing-client support calls she'll redirect to you), and for Flow B later.
4. **Ad launch date** — confirms my deadline. I've assumed roughly 5 weeks from now (around 2026-06-12) — if you're aiming sooner, we can compress, but I'd want a couple more weeks of test calls before letting paid traffic anywhere near it.

**Estimated timeline**

| Week                   | Milestone                                                                                                  |
| ---------------------- | ---------------------------------------------------------------------------------------------------------- |
| Week 1 (this week)     | Vapi account + UK number provisioned, Calendly + Anthropic keys in, server-side scaffolding started        |
| Week 2 (12-18 May)     | DB migration shipped, Vapi assistant config + opening flow in, end-to-end Flow A working in test           |
| Week 3 (19-25 May)     | SMS confirms send, hangup recovery, callback fallback, "Call History" admin view in                        |
| Week 4 (26 May-1 Jun)  | You and I run test calls, tune voice + prompts, ICO + privacy squared, soft launch (number live, ads paused) |
| Week 5 (2-8 Jun)       | Meta ads go live with Riley as the answering agent. Monitor + tune.                                        |

That's a 5-week runway to a real go-live around **2026-06-12**. The faster I get the Vapi credentials and Calendly token, the more buffer we have at the end.

I'll have a working test number you can call by the end of next week.

Kind Regards,

Luke Carter
Teventis | AI Automation Specialist
Email: Luke@teventis.com
Website: Teventis.com
Phone: 020 3411 0868

---

## Notes for Luke (not part of the email)

- Skim once before sending — the timeline is a forecast, soften or sharpen depending on what you've discussed verbally.
- Vapi account ask is now item #1 — explicit instruction to him to go set it up.
- Dropped the ICO question, the SMS sender question, the privacy policy question, and the 15-vs-30-min flag. ICO + privacy fold into the soft-launch week. SMS sender is your call. Calendar slug isn't a Rio question — the website is the source of truth.
