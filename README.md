# MachReach Student

> **Proprietary Software** — © 2026 MachReach. All rights reserved. See [LICENSE](LICENSE).

An all-in-one study platform for students.

## What It Does
- **Focus sessions** — distraction-free study timers, plus an optional Focus Guard browser extension that blocks distracting sites during a session
- **Canvas LMS import** — connect Canvas to sync your courses (also the bot-resistant signup gate)
- **AI flashcards & quizzes** — generated from your own course materials
- **Grade tracking** — Chilean 1.0–7.0 scale, with "minimum grade to pass" math
- **Gamification** — XP, streaks, leaderboards, badges, coins, daily quests, and friends
- **Referrals** — invite a friend, earn a free week of Plus (stacks)
- **Plus subscription** — unlimited AI generation and perks, billed via Lemon Squeezy

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python app.py          # or run_local.bat on Windows
```

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest       # covers the money paths: subscriptions, referrals, focus, badges
```

## Architecture
```
app.py                   - Flask app shell: auth, layout, billing webhook, public/legal pages
student/                 - Student dashboard, study tools, gamification, and APIs (registered as routes)
outreach/                - Internal legacy-named package containing shared config, DB, i18n, and billing infrastructure (no Outreach product routes)
worker.py                - Student AI jobs, plan maintenance, streak reminders, and leaderboard reporting
extensions/focus-guard   - Focus Guard browser extension
static/machreach_landing - Pre-built, pre-rendered React landing (see landing_build/)
landing_build/           - Local-only landing build (esbuild + jsdom prerender; output committed)
tests/                   - Pytest suite for the revenue-critical paths
docs/                    - Pitch and pricing docs
```

## Deployment
Runs on Render (`render.yaml`): a single gunicorn web service + a worker service, backed by Postgres.

Production release, backup/restore, incident, rollback, retention, and alert procedures are documented in [docs/operations.md](docs/operations.md).
Locally it falls back to SQLite when `DATABASE_URL` is unset.
