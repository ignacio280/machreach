# MachReach Student

> **Proprietary Software** - (c) 2026 MachReach. All rights reserved. See [LICENSE](LICENSE).

AI-powered study tools for students.

## What It Does
- Generates study plans, quizzes, flashcards, and notes from student materials
- Provides an AI tutor, essay feedback, panic-mode planning, and practice tools
- Tracks focus sessions, XP, streaks, leaderboards, badges, and coins
- Includes a student marketplace for sharing and buying study files
- Supports Canvas LMS import and the optional Focus Guard browser extension

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python app.py
```

## Architecture
```
app.py              - Flask app shell, auth, layout, billing webhook, shared pages
student/            - Student dashboard, study tools, marketplace, settings, APIs
outreach/           - Shared infrastructure modules such as config, DB, mail, and billing helpers
extensions/         - Focus Guard browser extension
docs/               - Project docs
```
