# AI Email Outreach Agent

Autonomous AI-powered cold email outreach system for small businesses.

## What It Does
- Generates personalized cold email sequences using AI (OpenAI GPT)
- Sends emails on a schedule (drip campaigns)
- Tracks opens, replies, and bounces
- A/B tests subject lines automatically
- Web dashboard for clients to manage campaigns

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python -m outreach.db   # initialize database
python app.py           # start web dashboard
python worker.py        # start email worker (separate terminal)
```

## Architecture
```
app.py              — Flask web dashboard (client-facing)
worker.py           — Background email sender (scheduler)
outreach/
  config.py         — Settings, env vars
  db.py             — SQLite database (campaigns, contacts, emails)
  ai.py             — GPT email generation (sequences, A/B variants)
  sender.py         — SMTP email delivery with tracking
  tracker.py        — Open/click tracking pixel + webhook
  templates/        — HTML email templates
```

## Pricing Model
- $200-500/month per client
- 10 clients = $2k-5k/month recurring
