"""
AI-powered course file analyzer — reads syllabi, course programs, and
assignment descriptions to extract structured data:
  - Test/exam dates and what they cover
  - Weekly topic schedule
  - Grading weights
  - Required readings

Uses OpenAI GPT to parse unstructured text into JSON.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any

from openai import OpenAI

from outreach.config import OPENAI_API_KEY

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def _ai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


# ── main extraction ─────────────────────────────────────────

def analyze_course_material(
    course_name: str,
    syllabus_html: str = "",
    file_texts: list[dict] | None = None,
    assignments: list[dict] | None = None,
) -> dict:
    """
    Analyze all available material for a single course and return structured data.

    Parameters:
        course_name:   e.g. "Cálculo II"
        syllabus_html: Raw HTML from Canvas syllabus tab
        file_texts:    [{"filename": "...", "text": "..."}]  — extracted text from PDFs/DOCX
        assignments:   Raw Canvas assignment objects

    Returns:
        {
            "course_name": str,
            "exams": [{"name", "date", "weight_pct", "topics": [...]}],
            "weekly_schedule": [{"week", "dates", "topics": [...]}],
            "grading": {"component": weight_pct, ...},
            "key_dates": [{"date", "description"}],
            "study_tips": [str]
        }
    """
    # Build a rich context prompt from all available material
    context_parts: list[str] = []

    if syllabus_html:
        # Strip HTML tags for the AI
        clean = re.sub(r"<[^>]+>", " ", syllabus_html)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            context_parts.append(f"=== SYLLABUS (from Canvas) ===\n{clean[:8000]}")

    for ft in (file_texts or []):
        text = ft.get("text", "")[:6000]
        if text:
            context_parts.append(f"=== FILE: {ft['filename']} ===\n{text}")

    if assignments:
        asgn_text = _format_assignments(assignments)
        if asgn_text:
            context_parts.append(f"=== ASSIGNMENTS/EXAMS ===\n{asgn_text}")

    if not context_parts:
        return _empty_result(course_name)

    full_context = "\n\n".join(context_parts)

    prompt = f"""You are an academic assistant analyzing course material for "{course_name}".
From the provided material, extract and return ONLY valid JSON with this exact structure:

{{
  "course_name": "{course_name}",
  "exams": [
    {{
      "name": "Midterm 1",
      "date": "YYYY-MM-DD or null if unknown",
      "weight_pct": 25,
      "topics": ["Topic 1", "Topic 2"]
    }}
  ],
  "weekly_schedule": [
    {{
      "week": 1,
      "dates": "Mar 3 - Mar 7",
      "topics": ["Introduction", "Chapter 1"]
    }}
  ],
  "grading": {{
    "Midterm 1": 25,
    "Midterm 2": 25,
    "Final Exam": 30,
    "Homework": 20
  }},
  "key_dates": [
    {{
      "date": "YYYY-MM-DD",
      "description": "Last day to drop"
    }}
  ],
  "study_tips": [
    "Focus heavily on chapters 3-5 for the first midterm",
    "Practice problems recommended weekly"
  ]
}}

Rules:
- Extract ALL exams/tests/quizzes with their dates and what topics they cover
- If dates use a non-standard format, convert to YYYY-MM-DD
- If the document is in Spanish, still output JSON keys in English but keep topic names in their original language
- weight_pct should be the percentage of the final grade (integer)
- If information is missing, use null for dates and empty arrays for topics
- study_tips should be actionable advice based on the grading weights and exam structure
- Return ONLY the JSON, no markdown fences, no explanation

COURSE MATERIAL:
{full_context}"""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        data["course_name"] = course_name
        return data
    except json.JSONDecodeError:
        log.error("AI returned invalid JSON for %s", course_name)
        return _empty_result(course_name)
    except Exception as e:
        log.error("AI analysis failed for %s: %s", course_name, e)
        return _empty_result(course_name)


def generate_study_plan(
    courses_data: list[dict],
    preferences: dict | None = None,
) -> dict:
    """
    Given analyzed data for ALL courses, generate a unified study plan.

    Parameters:
        courses_data: List of analyze_course_material() results
        preferences: {
            "hours_per_day": 4,
            "preferred_time": "morning",
            "work_schedule": {...},
            "weak_subjects": ["Cálculo"],
            "exam_prep_days": 7,
        }

    Returns:
        {
            "daily_plan": [{
                "date": "YYYY-MM-DD",
                "day_name": "Monday",
                "sessions": [{
                    "course": str,
                    "topic": str,
                    "hours": float,
                    "type": "study|review|exam_prep",
                    "priority": "high|medium|low",
                    "reason": str
                }]
            }],
            "upcoming_exams": [{...}],
            "weekly_summary": str,
            "recommendations": [str]
        }
    """
    prefs = preferences or {}
    hours_per_day = prefs.get("hours_per_day", 4)
    prep_days = prefs.get("exam_prep_days", 7)
    weak = prefs.get("weak_subjects", [])

    # Build exam timeline
    all_exams = []
    for cd in courses_data:
        for exam in cd.get("exams", []):
            all_exams.append({
                "course": cd["course_name"],
                "name": exam.get("name", "Exam"),
                "date": exam.get("date"),
                "weight_pct": exam.get("weight_pct", 0),
                "topics": exam.get("topics", []),
            })

    courses_summary = json.dumps(courses_data, ensure_ascii=False, indent=2)[:12000]
    prefs_str = json.dumps(prefs, ensure_ascii=False)

    today = date.today().isoformat()

    prompt = f"""You are an expert academic study planner. Today is {today}.

A student has these courses with their exam schedules and topics:

{courses_summary}

Student preferences: {prefs_str}

Create a detailed daily study plan for the NEXT 14 DAYS ONLY (from {today}).
Return ONLY valid JSON:

{{
  "daily_plan": [
    {{
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "total_hours": 4,
      "sessions": [
        {{
          "course": "Course Name",
          "topic": "Specific topic to study",
          "hours": 2.0,
          "type": "study",
          "priority": "high",
          "reason": "Exam in 5 days, covers this topic"
        }}
      ]
    }}
  ],
  "upcoming_exams": [
    {{
      "course": "Course Name",
      "name": "Midterm 1",
      "date": "YYYY-MM-DD",
      "days_until": 12,
      "weight_pct": 25,
      "prep_status": "On track"
    }}
  ],
  "recommendations": [
    "Start reviewing Cálculo chapters 3-5 immediately — exam is in 8 days",
    "Physics labs count for 20% — don't skip them"
  ]
}}

Rules:
- ONLY 14 days of daily_plan entries, no more
- Allocate {hours_per_day}h per day max
- Start intensive review {prep_days} days before each exam
- Weight study time by exam weight (a 40% final gets more time than a 10% quiz)
- Weak subjects get 50% more study time: {weak}
- Alternate between subjects to avoid burnout (max 3h same subject)
- Include specific topics, not just "study for exam"
- Sunday is a lighter day (half study time)
- Mark priority based on how close the exam is and its weight
- Keep each session description concise (under 15 words)
- Return ONLY JSON, no markdown fences"""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=16000,
        )
        choice = resp.choices[0]
        raw = choice.message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        # Handle truncated JSON (finish_reason == 'length')
        if getattr(choice, 'finish_reason', None) == 'length':
            log.warning("Study plan response was truncated, attempting repair")
            # Try to close any open arrays/objects
            for closer in [']}]}', ']}', '}]}'  , ']}]}']:
                try:
                    return json.loads(raw + closer)
                except json.JSONDecodeError:
                    continue
            raise ValueError("AI response was truncated and could not be repaired")

        return json.loads(raw)
    except Exception as e:
        log.error("Study plan generation failed: %s", e)
        raise RuntimeError(f"Plan generation failed: {e}")


# ── helpers ─────────────────────────────────────────────────

def _format_assignments(assignments: list[dict]) -> str:
    """Format Canvas assignments into readable text for the AI."""
    lines = []
    for a in assignments:
        name = a.get("name", "Untitled")
        due = a.get("due_at", "No due date")
        pts = a.get("points_possible", "?")
        desc = a.get("description", "") or ""
        desc_clean = re.sub(r"<[^>]+>", " ", desc)[:300].strip()
        lines.append(f"- {name} | Due: {due} | Points: {pts}")
        if desc_clean:
            lines.append(f"  Description: {desc_clean}")
    return "\n".join(lines)


def _empty_result(course_name: str) -> dict:
    return {
        "course_name": course_name,
        "exams": [],
        "weekly_schedule": [],
        "grading": {},
        "key_dates": [],
        "study_tips": [],
    }
