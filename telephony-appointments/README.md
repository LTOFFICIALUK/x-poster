# Riley — Teventis AI Phone Agent

Inbound AI phone agent that answers calls placed to a Vapi-provisioned UK number, qualifies the lead, books a discovery call into Rio's Calendly, and SMS-confirms. Lives inside `teventis-automations/` so it shares the same deploy pipeline, virtualenv, and environment file as the news-automation service.

See [`../telephony-appointments/REQUIREMENTS.md`](../telephony-appointments/REQUIREMENTS.md) (the working spec) for scope, qualification questions, edge-case handling, and the full project plan.

## File map

```
telephony-appointments/
├── server.py            # FastAPI app — POST /vapi/webhook + GET /health
├── vapi_assistant.py    # One-shot deploy script — pushes Riley's config to Vapi
├── riley_prompt.py      # Canonical system prompt + opening line + service slugs
├── telephony_db.py      # DB layer — calls, transcripts, callback requests, config
├── calendly_client.py   # Calendly REST wrapper — availability + single-use links
├── vapi_client.py       # Vapi REST wrapper — assistant CRUD + SMS
├── requirements.txt     # Additional pip deps (fastapi, uvicorn)
├── .env.template        # Additional env vars (merged into parent .env)
├── teventis-telephony.service  # systemd unit (installs to /etc/systemd/system/)
└── README.md            # this file
```

## Local dev

Install deps into the parent venv (one venv shared with news automation):

```bash
cd /Users/lukecarter/Documents/Claude/Projects/AI\ Automation\ Specialist/teventis-automations
source venv/bin/activate    # or python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -r telephony-appointments/requirements.txt
```

Make sure the database has the new tables:

```bash
cd ../teventis-database
make db-migrate    # applies migrations/004_telephony.sql
```

Set env vars in `teventis-automations/.env` (copy `.env.template` from both this dir and the parent if you don't have one yet). Then:

```bash
cd telephony-appointments
python server.py
# → INFO ... Uvicorn running on http://0.0.0.0:8088
```

For Vapi to reach your local server, expose it with ngrok:

```bash
ngrok http 8088
# Take the https URL and set TELEPHONY_WEBHOOK_URL=https://xxx.ngrok.app/vapi/webhook
```

## Deploying Riley's assistant config to Vapi

After you've set `VAPI_API_KEY`, `TELEPHONY_WEBHOOK_URL`, and inserted a telephony automation row in the DB:

```bash
# Inspect the payload first — no API call
python vapi_assistant.py print

# Then push to Vapi (creates new assistant + saves id, or PATCHes existing)
python vapi_assistant.py upload

# Sanity check what's live vs. what's in the repo
python vapi_assistant.py diff
```

The script writes the resulting `vapi_assistant_id` back to `telephony_automation_configs` so the next run is an update, not another create.

## Seeding the first automation row

Until the admin UI grows a "create telephony automation" form, seed manually. From `teventis-database/`:

```sql
-- 1. Pick or create the client (Teventis itself, in this case)
INSERT INTO clients (slug, name, status)
VALUES ('teventis', 'Teventis', 'active')
ON CONFLICT (slug) DO NOTHING;

-- 2. Add a telephony automation
INSERT INTO automations (client_id, type, name, status, timezone)
SELECT id, 'telephony_appointments', 'Riley — inbound bookings', 'active', 'Europe/London'
FROM clients WHERE slug = 'teventis'
RETURNING id;
-- ↑ note the returned id; use as <AUT_ID> below

-- 3. Add the per-automation telephony config
INSERT INTO telephony_automation_configs (
  automation_id,
  calendly_event_url,
  escalation_phone_e164,
  opening_line
)
VALUES (
  <AUT_ID>,
  'https://calendly.com/rio-teventis/30min',
  '+44...',                       -- Rio's mobile, E.164
  ''                              -- empty = use riley_prompt.OPENING_LINE
);
```

Then run `python vapi_assistant.py upload` to provision the assistant on Vapi.

## Production

The systemd unit `teventis-telephony.service` runs `server.py` under `/opt/teventis-automations/venv/bin/python`. Both `teventis-automations` (news scheduler) and `teventis-telephony` (this) share the same working dir, venv, and `.env`.

Install once:

```bash
sudo cp teventis-telephony.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable teventis-telephony
sudo systemctl start teventis-telephony
sudo journalctl -u teventis-telephony -f
```

The deploy workflow (`.github/workflows/deploy.yml`) is updated to restart both services after `git pull`.

A reverse proxy (Caddy or nginx) terminates HTTPS for `riley.teventis.com` and forwards `/vapi/webhook` to `localhost:8088`. Caddy one-liner:

```
riley.teventis.com {
  reverse_proxy localhost:8088
}
```

## Open issues / TODOs

These are flagged inline in the code with `TODO:` comments — most are "verify the exact Vapi/Calendly endpoint shape against current docs once we have credentials":

- `calendly_client.py` — confirm `/event_type_available_times` parameter shape, confirm `/scheduling_links` body shape.
- `vapi_client.py` — confirm `/sms` endpoint and field names; confirm `phone-number` PATCH shape.
- `vapi_assistant.py` — confirm Vapi accepts Anthropic models directly; pick the actual ElevenLabs voice id during testing.
- `server.py` — `_normalise_transcript` accepts multiple shapes Vapi has used historically; trim to whatever the live API actually sends.
- Calendly booking confirmation webhook — currently we mark `outcome='booked'` optimistically when we send the SMS. The proper flip from "invitation sent" → "booked" needs a `/calendly/webhook` endpoint listening for `invitee.created` events.
