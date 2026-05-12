# Credentials Needed — Telephony Build

Everything required to run Riley locally and in production. Group A is what you need to ask Rio for; Group B is stuff you can self-serve; Group C is decisions / values you set yourself.

## A — Rio needs to send these to you

| What                          | Where to get it                                                              | Goes into                                                       |
| ----------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **Vapi API key**              | Rio creates a Vapi account at dashboard.vapi.ai → Settings → API Keys        | `.env` → `VAPI_API_KEY`                                         |
| **Calendly Personal Access Token** | Rio's Calendly account → Settings → Integrations → API & Webhooks → Generate token | `.env` → `CALENDLY_PAT`                                       |
| **Rio's mobile (E.164)**      | Just get the number. E.164 means `+44...` form, no spaces                    | DB → `telephony_automation_configs.escalation_phone_e164`       |
| **Vapi UK phone number id + E.164** | Auto-generated when Rio (or you) provisions a UK DDI inside the Vapi dashboard. Capture both the internal id (`pn_...`) and the rendered number (`+44...`) | DB → `telephony_automation_configs.vapi_phone_number_id` and `vapi_phone_number_e164` |

## B — You provision yourself

| What                          | Where to get it                                                              | Goes into                                                       |
| ----------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **Anthropic API key (project-scoped)** | console.anthropic.com → Settings → API Keys → "Create key" with a project label like "telephony" | `.env` → `ANTHROPIC_API_KEY` (or a separate `TELEPHONY_ANTHROPIC_API_KEY` if you want clean cost separation) |
| **`TEVENTIS_SECRETS_KEY`**    | Already exists for news automation. Reuse it. Do **not** generate a new one — that would invalidate every encrypted credential currently in `social_account_credentials`. | `.env` (already set on the server) |
| **`TELEPHONY_WEBHOOK_SECRET`** | Generate: `openssl rand -hex 32`                                            | `.env` → `TELEPHONY_WEBHOOK_SECRET`, AND paste the same value into Vapi's assistant config under `serverUrlSecret` |
| **GitHub Actions secrets**    | Already set on `teventis-automations` repo: `SERVER_HOST`, `SSH_PRIVATE_KEY`. No new ones needed for telephony — same deploy job. | GitHub repo → Settings → Secrets |
| **DNS for `riley.teventis.com`** | Wherever Teventis's DNS lives (Cloudflare? Namecheap?). Add an A record pointing to `178.104.30.166` | DNS provider                                                    |
| **HTTPS cert**                | Caddy auto-handles via Let's Encrypt — just put a `riley.teventis.com { reverse_proxy localhost:8088 }` block in the server's `Caddyfile` | Server config                                                   |

## C — Decisions / values you set

| What                          | Default for v1                                                               | Goes into                                                       |
| ----------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `TELEPHONY_WEBHOOK_URL`       | `https://riley.teventis.com/vapi/webhook`                                     | `.env`                                                          |
| `TELEPHONY_PORT`              | `8088`                                                                        | `.env`                                                          |
| Recording retention (days)    | 30 (already decided)                                                          | DB → `telephony_automation_configs.recording_retention_days`    |
| Transcript retention (days)   | 365 (already decided)                                                         | DB → `telephony_automation_configs.transcript_retention_days`   |
| Calendly event URL            | `https://calendly.com/rio-teventis/30min` (from teventis.com)                 | DB → `telephony_automation_configs.calendly_event_url`          |
| Opening line                  | Empty (uses `riley_prompt.OPENING_LINE`)                                      | DB → `telephony_automation_configs.opening_line` (override only) |
| System prompt override        | Null (uses `riley_prompt.SYSTEM_PROMPT`)                                      | DB → `telephony_automation_configs.system_prompt_override`      |
| ElevenLabs voice id           | Placeholder in `vapi_assistant.py` — pick during voice tests in week 1        | `vapi_assistant.py` `voice.voiceId`                             |

## How they get installed

1. **Locally** — copy `teventis-automations/.env.template` to `.env` and fill in. Both `server.py` and `vapi_assistant.py` `load_dotenv()` from there.
2. **On the server (`178.104.30.166`)** — there's already an `.env` at `/opt/teventis-automations/.env` that the news scheduler uses. Append the new telephony vars to that same file. Both services share it.
3. **Vapi dashboard** — paste `TELEPHONY_WEBHOOK_URL` + `TELEPHONY_WEBHOOK_SECRET` into the assistant's "Server" settings (or let `vapi_assistant.py upload` push them via the API).

## Order of operations once Rio sends Vapi access

```bash
# 1. Apply the migration to staging + prod DB
cd teventis-database
make db-migrate

# 2. Insert the Teventis client + telephony automation row + config row
#    (one-off — see telephony-appointments/README.md "Seeding" section)
psql $DATABASE_URL -f telephony-appointments/seed_first_automation.sql   # not yet written

# 3. Push Riley's assistant config to Vapi
cd ../teventis-automations
source venv/bin/activate
pip install -r requirements.txt
cd telephony-appointments
python vapi_assistant.py upload

# 4. (Server) install + start the systemd unit
sudo cp teventis-telephony.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teventis-telephony

# 5. Make a test call from your mobile to the Vapi UK number
```

If anything fails, `journalctl -u teventis-telephony -f` is the first place to look.
