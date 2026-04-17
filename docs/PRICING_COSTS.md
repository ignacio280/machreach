# MachReach — cost inventory (what eats your money)

Use this to size each subscription tier. Prices are approximate USD at time
of writing — verify with each vendor before publishing.

## 1. OpenAI API (biggest variable cost)

Used by:
- `outreach/ai.py` — subject-line optimizer, reply intelligence,
  deliverability checker, campaign drafting.
- `student/analyzer.py` — course syllabus analyzer, exam topic extractor,
  essay assistant, panic / cram-plan generator, flashcard generation.

Model in use: **gpt-4o-mini** (cheap tier).
- Input  ≈ $0.15 / 1M tokens
- Output ≈ $0.60 / 1M tokens

Rough per-call cost:
| Feature              | tokens in/out | ~cost/call |
|----------------------|---------------|------------|
| Subject optimizer    | 300 / 300     | $0.00023   |
| Reply intelligence   | 500 / 400     | $0.00032   |
| Deliverability check | 800 / 500     | $0.00042   |
| Syllabus analyzer    | 6k  / 1.5k    | $0.00180   |
| Essay assistant      | 2k  / 1k      | $0.00090   |
| Cram plan (panic)    | 1k  / 1.5k    | $0.00105   |
| Flashcard generator  | 1.5k / 1k     | $0.00083   |

**Expected monthly per active student:** $0.30 – $1.50 heavy usage.
**Expected monthly per active business user:** $0.80 – $3.00.

Set a hard monthly ceiling per account (see `PLAN_LIMITS` in
`outreach/config.py`) to prevent abuse.

## 2. Email sending (system + campaign mail)

Used by:
- `app.py` `send_system_email` — SMTP for password resets / notifications.
- Campaign sender in `outreach/sender.py`.

Options:
- Gmail / Google Workspace SMTP: free up to ~500/day, tied to mailbox.
- SendGrid: $19.95/mo for 50k sends.
- Resend: $20/mo for 50k sends.
- AWS SES: $0.10 per 1k emails (cheapest at scale).

**Budget:** ~$20/mo fixed + $0.0001 per send if you go SES.

## 3. Hosting (Render)

- Web service (Flask + gunicorn): $7/mo Starter, $25/mo Standard.
- Background worker (`worker.py`): $7/mo.
- Postgres: $7/mo Starter (1 GB), $20/mo Standard (10 GB).
- Redis (if added for rate limits): $10/mo.

**Monthly floor:** ~$21 (1 web + 1 worker + 1 DB starter). Scale to
~$55 once you have real traffic.

## 4. Domain + DNS

- Domain (.com): $12/yr.
- Cloudflare / Route53: $0–$5/mo.

## 5. Canvas API

Free (students bring their own API token).

## 6. Alpaca (financo only — not MachReach)

Separate project, separate cost. Paper trading is free; live trading
has per-share commissions depending on tier.

## 7. Stripe (once you charge)

- 2.9% + $0.30 per successful card charge.
- No monthly fee.

## 8. File storage

Course file uploads live on Render's disk today. For scale, move to
S3 / R2:
- S3: $0.023/GB/mo + egress.
- Cloudflare R2: $0.015/GB/mo + **no egress**.

**Budget:** $1–10/mo for first 1k users.

---

## Suggested pricing tiers

Given roughly $0.80–$2.00 AI spend per active student/month and
~$30/mo in fixed infra before 100 users:

| Plan        | Price   | AI cap        | Notes                           |
|-------------|---------|---------------|---------------------------------|
| Free        | $0      | $0.20/mo worth| Syllabus + 5 essay/panic runs   |
| Student Pro | $5/mo   | $2.00/mo      | Unlimited focus, full rank sys  |
| Business    | $29/mo  | $6.00/mo      | Campaign AI + reply intel       |
| Business+   | $99/mo  | $25/mo        | Bulk send, priority             |

Keep a `monthly_ai_spend_cents` column and cut off at the cap.
