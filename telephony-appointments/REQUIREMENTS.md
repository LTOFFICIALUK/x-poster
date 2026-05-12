# Telephony Appointment Setter — Requirements & Open Questions

**Project codename:** Riley from Teventis
**Owner:** Luke Carter
**Stakeholder:** Rio Sanderson
**Started:** 2026-05-08
**Target go-live:** TBD — gated by Rio's Meta ad launch date (need confirmation)

This is the working doc. Decisions go in the **Decisions** section, blocking unknowns go in **Open questions**, post-it items go in **Todo**. Update as we build.

---

## 1. Scope

Riley answers calls placed to a Vapi-provisioned UK number that will be displayed in Rio's Meta ad campaign. Her one job: qualify the lead, get them booked into Rio's Calendly for a 30-minute discovery call, confirm by SMS.

**She does:**

- Answer inbound calls 24/7 (calendar availability is enforced by Calendly's free/busy)
- Identify which of Teventis's five services the caller is interested in (or surface "not sure" → route to discovery anyway)
- Capture name, business name, role, phone, email, location
- Light qualification — 2–3 questions per service, no audit
- Pull live free/busy from Rio's Calendly, offer 2–3 slot options
- Book the slot via Calendly API
- Send SMS confirmation with the meeting link
- Record + transcribe every call to our DB

**She does NOT:**

- Quote prices — ever. "Pricing is tailored to your business — Rio will walk you through it on the call."
- Discuss support, complaints, refunds, or anything contractual
- Take inbound calls about anything that isn't the five services
- Provide medical, legal, or financial advice
- Book without an email and a mobile number

---

## 2. Flow A — inbound (shipping first)

```
Caller dials Teventis number
  → Riley greets + states recording disclosure (≤ 8s, opening before any user speech)
  → Asks how she can help / which service brought them in
  → Captures contact details (name, business, role, email, mobile, postcode/region)
  → Light qualification (see §4)
  → Pulls live Calendly availability for next 7 days
  → Offers up to 3 slot options ("Tuesday 3pm, Wednesday 10am, or Friday 2pm — which works?")
  → Books the slot via Calendly API
  → Reads back the booked time + confirms SMS will arrive momentarily
  → Sends SMS with Calendly meeting URL + ICS link
  → Closes warmly
  → Webhook fires → backend persists transcript + recording + lead row
```

## 3. Flow B — callback (post-launch A/B test, not in scope for v1)

```
New lead → Riley calls Rio's mobile first ("got a lead for [service], can you take it?")
  → If Rio accepts: bridge the call
  → If Rio declines / doesn't pick up within 15s: Riley calls the lead back, runs Flow A
```

Build with hooks for this from day one but don't enable until v1 Flow A is stable and Rio agrees.

---

## 4. Qualification questions per service

Riley should ask **no more than three** qualifiers per service before booking. The discovery call with Rio does the real audit — Riley's job is just to confirm the caller is in scope and capture enough context for Rio to prep.

**Always asked (regardless of service):**

1. *"Could I take your name and the name of your business?"*
2. *"What's the best email to send the booking confirmation to?"* — single contact only, less to mishear. We capture the **mobile from the caller ID automatically** via Vapi, no need to ask.
3. *"Where are you based?"* — captures region (postcode is enough). Used for context, not as a hard filter — see §5.

**Lead Recovery** (`/services/lead-recovery`)
- *"Roughly how many enquiries do you reckon are sitting unused in your inbox or CRM?"* — one of: under 50 / 50–500 / 500+ / not sure.
- *"What system do you currently use to track them — a CRM, just your inbox, something else?"*
- *"When did you last reach back out to your old enquiries?"*

**Targeted Lead Generation** (`/services/lead-generation`)
- *"Are you currently running paid ads, or is this your first time?"*
- *"Roughly what monthly ad budget are you working with?"* — open-ended; allow "not sure yet".
- *"Which service or offer are you trying to fill?"*

**Automated Follow-Up** (`/services/automated-followup`)
- *"How do new enquiries usually come in — your website, Instagram, missed calls, walk-ins?"*
- *"Who's responsible for replying to them now?"*
- *"How fast can you typically reply at the moment?"*

**AI Appointment Booking** (`/services/ai-appointment-booking`)
- *"What calendar tool do you use — Google, Outlook, Calendly, or something else?"*
- *"Are you mostly booking class slots or one-to-one sessions?"*
- *"Roughly how many bookings do you take per day?"*

**Website Chat Assistant** (`/services/chat-assistant`)
- *"Do you already have a website you'd want this connected to?"*
- *"Roughly how many visitors do you get per month — even a rough guess?"*
- *"Do you currently use any chat tool, or would this be a first?"*

**"Not sure which service / I just saw the ad":**

Don't push them down a service path. Ask: *"What's the biggest bottleneck in your business right now — getting more enquiries, or converting the ones you already have?"* Capture answer, book the call, let Rio diagnose.

---

## 5. Edge cases — default to a Rio callback before turning anyone away

Riley's job is to keep leads warm, not to bounce them. If a caller doesn't fit the standard Flow A path for any reason, the default fallback is:

> *"Let me get Rio to call you back directly — would that work?"*

If yes: capture name + mobile (auto from caller ID) + email + a one-line note on what they were calling about, log a callback request, SMS Rio's mobile. **No Calendly slot booked** — Rio handles it personally.

This applies to:

- **Caller is outside the UK** — don't disqualify. Offer the Rio callback. We may want the work even if they're abroad; let Rio decide.
- **Existing client with a support issue** — offer the Rio callback. *"Let me get Rio to call you back directly, would that work?"*
- **Anything Riley can't confidently handle** — odd questions, unclear requests, language barriers — fall back to the Rio callback rather than guessing.

**Genuine declines (still polite, no callback offered):**

- **Recruitment / vendor / cold sales** — *"We're not taking sales calls through this line — best to email Hello@teventis.com."*
- **Anything legal / medical / financial** — *"That's outside what I can help with on this call — I'd recommend speaking to a qualified professional."*

Rule of thumb: if the lead might be worth Rio's time, offer the callback. If it's clearly outside our world (recruitment, advice we're not qualified to give), polite redirect to email.

---

## 6. Stack

| Layer            | Choice                              | Status                                       |
| ---------------- | ----------------------------------- | -------------------------------------------- |
| Telephony        | Vapi (UK DDI provisioned via Vapi)  | Account creation requested from Rio          |
| LLM              | Claude (Anthropic)                  | Project key TBD                              |
| TTS              | Vapi default → ElevenLabs if needed | TBD — pick post-test                         |
| STT              | Vapi default (Deepgram-bundled)     | TBD                                          |
| Calendar         | Calendly API → Rio's discovery link | PAT requested from Rio                       |
| SMS              | Vapi-bundled SMS                    | Long-code default; revisit after first 50 calls |
| Backend          | Python (FastAPI), lives in `teventis-automations/telephony-appointments` | Auto-deploys via existing `.github/workflows/deploy.yml` |
| Database         | Postgres in `teventis-database`     | Add new tables via migration; update canonical `schema.sql` |
| Admin surface    | New "Call History" view in `frontend-admin` | To build alongside backend |
| Webhook hosting  | Same VPS, sub-path on the existing deployed service | No new subdomain needed for v1 |

**Sits inside the existing stack:**

- **Code** lives in `teventis-automations/telephony-appointments/` as a sibling to the news automation. Pushing to `main` triggers `appleboy/ssh-action` → SSH to `/opt/teventis-automations` → `git pull` → `pip install -r requirements.txt` → `systemctl restart teventis-automations`. We register Riley's webhook routes inside the existing FastAPI service.
- **Database** uses the existing `teventis-database` repo. Per its agent notice: edit `schema.sql` first to reflect the full desired schema, then add a numbered migration (`004_telephony.sql` next), then update consumers. New tables (provisional): `calls`, `call_transcripts`, `callback_requests`. We register a new `automation` row of type `telephony_appointments` for Rio's account so it slots into the existing client/automation model.
- **Admin** uses `frontend-admin` — add a "Call History" item to the sidebar nav, with per-client filtering matching the existing client-scoped pages.

**Server:** `178.104.30.166` (Hetzner). Root credentials stored in secrets manager — never in this repo. SSH key + secrets are already wired up via `SERVER_HOST` and `SSH_PRIVATE_KEY` GitHub Actions secrets on the existing automations repo.

---

## 7. Riley's persona

| Trait        | Setting                                                                  |
| ------------ | ------------------------------------------------------------------------ |
| Name         | Riley                                                                    |
| Affiliation  | "from Teventis"                                                          |
| Voice        | Female, calm, professional, warm, **not** robotic or stiff               |
| Pace         | Deliberate, not rushed. Pauses naturally for the caller to think.        |
| Vocabulary   | UK English. "Mobile" not "cell". "Booked in" not "scheduled". No jargon. |
| Disclosure   | Recording disclosure must come within the first 8 seconds.               |
| Pricing      | Never quotes. Always defers to Rio on the discovery call.                |
| Confidence   | Never claims to be a human. If asked, "I'm Teventis's AI assistant."     |

### Opening greeting (≤ 8 seconds, must include recording disclosure)

> *"Hi, you've reached Teventis — I'm Riley, the AI assistant. Just so you know, this call's recorded so we can keep improving the service. How can I help today?"*

That's roughly 7.5 seconds at a normal speaking pace. Tweak if test calls show it lands long.

### Elevator pitch — used if the caller asks "what does Teventis actually do?"

Source: teventis.com hero + about page.

> *"We help service businesses — gyms, studios, that sort of thing — capture more revenue from the leads they're already getting. Most businesses lose enquiries because nobody can reply fast enough. We build the systems that follow up automatically, so every lead gets answered and the qualified ones land in your calendar. The discovery call with Rio is where we'd look at your specific setup and show you what we'd build."*

---

## 8. SMS confirmation copy (draft)

```
Hi {first_name}, this is Riley from Teventis confirming your call with Rio
on {date} at {time} (UK). Calendar invite + meeting link: {calendly_event_url}.
If anything changes, reply STOP or email Hello@teventis.com.
```

**SMS sender ID:** Long-code default for v1 (works on every UK network, no operator approval needed). Revisit alphanumeric "Teventis" branding after the first 50 calls if conversion looks weak. Luke's call.

**Callback-request SMS to Rio** (separate template for the §5 Rio-callback fallback):

```
Callback request — {first_name} ({mobile}) — re: {one_line_note}.
Email on file: {email}. Logged at {timestamp}.
```

---

## 9. Compliance

- **Recording disclosure** — required in opening line. Done in §7.
- **Retention** — recordings 30 days, transcripts 12 months. **Decided.**
- **Lawful basis** — legitimate interest for the call itself (caller voluntarily phoned in); explicit consent for marketing follow-up beyond the booked discovery call.
- **Processors** — Vapi, Anthropic, Calendly, ElevenLabs (if used).
- **ICO registration + privacy policy update** — deferred. Ads launch only once the build is complete; Luke will square these away during the soft-launch window before the number goes live to paid traffic. Privacy policy draft is already in the repo at `frontend-website/src/app/privacy/page.tsx` ready for solicitor review when we get there.

---

## 10. Decisions made (2026-05-08)

- **Flow A only for v1.** Flow B (callback to Rio first) is a post-launch A/B test.
- **Vapi over Twilio.** Bundled telephony + STT + TTS is faster to ship and the cost difference at our volume is small.
- **Calendly stays.** Rio already uses it; the API supports the use case fully.
- **Voice: female, calm, professional.** Riley as agent name. Specific TTS voice pick deferred until we test 2-3 options.
- **Recording on by default.** With disclosure in opening line.
- **Recording retention 30d, transcripts 12 months.**
- **Riley never quotes prices.** Always defers to Rio.
- **Scope is tight to the 5 services.** Out-of-scope or off-pattern calls fall back to a Rio-callback offer rather than a flat decline (see §5).
- **Email collected on call, mobile from caller ID.** One contact at a time to ask, less to mishear.
- **SMS sender ID: long code default for v1.**
- **Lives inside the existing stack.** Code in `teventis-automations/telephony-appointments/`, DB tables added via the existing `teventis-database` migration pattern, admin surface added to `frontend-admin` as a new "Call History" view.
- **Site, ICO, privacy compliance handled during soft-launch window.** Ads only launch once build is complete; no rush on these items now.

---

## 11. Open items — what's owed by whom

### Rio owes us (in the Friday rundown email)

1. **Vapi account** — please create the account and send credentials so we can provision the UK number and start building.
2. **Calendly Personal Access Token** — generated from Calendly account settings.
3. **Rio's mobile number** — for callback escalations from Riley today, and for Flow B later.
4. **Ad launch date** — drives the deadline.

### Luke / internal — still to decide

- Vapi vs. ElevenLabs for the actual TTS voice — test once Vapi account is up.
- Final database tables — provisional names: `calls`, `call_transcripts`, `callback_requests`. Edit `teventis-database/schema.sql` first, then write `migrations/004_telephony.sql`.
- Webhook URL pathing — sub-path on the existing FastAPI service (e.g. `POST /vapi/webhook`) is the simplest route. No new subdomain needed for v1.
- Spam/robocall filtering — Vapi has built-in screening; verify it's on by default.
- Behaviour when caller hangs up mid-booking — SMS them a direct Calendly link with a "call dropped, here's the page to book yourself in" message. Build this from day one.
- What if Calendly returns no availability in the next 7 days — fall back to the Rio-callback offer (§5).
- Anthropic API key — likely a fresh project-scoped key for cost tracking; not blocked on Rio.

---

## 13. Todo

**Blocked on Rio:**

- [ ] Get Vapi account + credentials (and provision the UK DDI through it)
- [ ] Get Calendly Personal Access Token
- [ ] Get Rio's mobile number for escalations + Flow B

**Code work — backend (lives in `teventis-automations/telephony-appointments/`):**

- [ ] Add new `automation` row of type `telephony_appointments` to seed
- [ ] Edit `teventis-database/schema.sql` + write `migrations/004_telephony.sql` for `calls`, `call_transcripts`, `callback_requests`
- [ ] FastAPI route: `POST /vapi/webhook` — handles call.started, call.ended, function-call hooks
- [ ] Calendly client: list available 30-min slots from Rio's link, book a slot, return event URL
- [ ] Vapi assistant config: opening + recording disclosure, system prompt, function tools (`get_availability`, `book_slot`, `log_callback_request`, `send_sms`)
- [ ] SMS confirmation send (Vapi-bundled SMS)
- [ ] Hangup-recovery: detect short/uncompleted call, SMS the caller a direct Calendly link
- [ ] Persistence: write call row, transcript row, recording URL, callback requests
- [ ] Wire to existing `.github/workflows/deploy.yml` — confirm `requirements.txt` is updated and the systemd service picks up the new routes

**Code work — admin (lives in `frontend-admin/`):**

- [ ] Add "Call History" item to `src/components/sidebar-nav.tsx`
- [ ] New route `(app)/calls/page.tsx` — list view, filterable by client, status (booked / callback / dropped), date range
- [ ] Detail view per call: audio player, transcript, captured fields (name, business, email, mobile, region, service interest), Calendly event link if booked
- [ ] Surface callback requests as a separate filter / status

**Voice + tuning:**

- [ ] Pick + test 3 candidate TTS voices, pick one
- [ ] Tune opening line for natural pace (target ≤ 8s)

**Ship:**

- [ ] End-to-end test calls (Luke first, then Rio)
- [ ] Soft launch (Vapi number live, ad campaign paused)
- [ ] Square away ICO registration + final privacy policy review during soft-launch window
- [ ] Monitor first 50 calls, tune, then go live with ads

---

## 14. Timeline (rough — to confirm with Rio)

| Week                  | Milestone                                                                                   |
| --------------------- | ------------------------------------------------------------------------------------------- |
| Week 1 (this week)    | Vapi account + UK number provisioned, Calendly PAT in, server-side scaffolding started      |
| Week 2 (12-18 May)    | DB migration shipped, Vapi assistant config + opening flow in, end-to-end Flow A in test    |
| Week 3 (19-25 May)    | SMS confirms send, hangup recovery, callback fallback, "Call History" admin view in         |
| Week 4 (26 May-1 Jun) | Test calls (Luke + Rio), tune voice + prompts, ICO + privacy squared, soft launch (number live, ads paused) |
| Week 5 (2-8 Jun)      | Ads live with Riley answering. Monitor + tune.                                              |

5-week runway from today (2026-05-08) puts go-live around **2026-06-12**. Compress or stretch once Rio confirms the ad date.

---

_Last updated: 2026-05-08 by Luke (with Claude)._
