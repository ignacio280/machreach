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
    schedule_settings: list[dict] | None = None,
    course_difficulties: dict | None = None,
    incomplete_assignments: list[dict] | None = None,
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
        schedule_settings: [{day: 0, hours: 4.0, free: False}, ...] per weekday
        course_difficulties: {"Course Name": 5, ...}  (1-5 scale)
        incomplete_assignments: [{"course": ..., "topic": ..., "hours": ...}, ...]

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
    difficulties = course_difficulties or {}
    incomplete = incomplete_assignments or []

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

    # Build schedule context
    schedule_ctx = ""
    if schedule_settings:
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        lines = []
        for s in schedule_settings:
            dn = day_names[s["day"]] if 0 <= s["day"] <= 6 else f"Day {s['day']}"
            if s.get("free"):
                lines.append(f"  - {dn}: FREE DAY (no study)")
            else:
                lines.append(f"  - {dn}: {s.get('hours', 0)}h available")
        schedule_ctx = "WEEKLY SCHEDULE (student-configured):\n" + "\n".join(lines)
    else:
        schedule_ctx = f"No weekly schedule set — use {hours_per_day}h per day as default."

    # Build difficulty context
    diff_ctx = ""
    if difficulties:
        diff_lines = [f"  - {name}: {level}/5" for name, level in difficulties.items()]
        diff_ctx = "\nCOURSE DIFFICULTY RATINGS (1=easy, 5=very hard):\n" + "\n".join(diff_lines)
        diff_ctx += "\nAllocate proportionally MORE study time to higher-difficulty courses."

    # Build incomplete assignments context
    incomplete_ctx = ""
    if incomplete:
        inc_lines = [f"  - [{a['date']}] {a['course']}: {a['topic']} ({a['hours']}h, was {a['priority']} priority)"
                     for a in incomplete[:20]]
        incomplete_ctx = "\nINCOMPLETE ASSIGNMENTS FROM PREVIOUS DAYS (must be rescheduled):\n" + "\n".join(inc_lines)
        incomplete_ctx += "\nThese MUST be included in the new plan — prioritize them."

    prompt = f"""You are an expert academic study planner. Today is {today}.

A student has these courses with their exam schedules and topics:

{courses_summary}

Student preferences: {prefs_str}

{schedule_ctx}
{diff_ctx}
{incomplete_ctx}

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
- RESPECT the weekly schedule: if a day is marked FREE, set total_hours=0 and sessions=[] for that day
- For non-free days, allocate EXACTLY the hours the student specified (not more)
- If no schedule is set, use {hours_per_day}h per day max
- Start intensive review {prep_days} days before each exam
- Weight study time by exam weight (a 40% final gets more time than a 10% quiz)
- Courses with higher difficulty ratings (1-5) get proportionally more study time
- Weak subjects get 50% more study time: {weak}
- If there are incomplete assignments from previous days, schedule them FIRST with high priority
- Alternate between subjects to avoid burnout (max 3h same subject)
- Include specific topics, not just "study for exam"
- Mark priority based on how close the exam is, its weight, AND course difficulty
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


# ── Flashcard generation ────────────────────────────────────

def generate_flashcards(
    course_name: str,
    topics: list[str] | None = None,
    source_text: str = "",
    count: int = 15,
) -> list[dict]:
    """
    Generate flashcards from course material using AI.

    Returns:
        [{"front": "question", "back": "answer"}, ...]
    """
    context = ""
    if source_text:
        context = f"\n\nSOURCE MATERIAL:\n{source_text[:10000]}"
    topics_str = ", ".join(topics) if topics else "all key concepts"

    prompt = f"""You are creating study flashcards for the course "{course_name}".
Topics to cover: {topics_str}
{context}

Generate exactly {count} flashcards. Each flashcard should:
- Have a clear, specific question on the front
- Have a concise but complete answer on the back
- Cover different aspects of the topics
- Progress from basic concepts to more advanced ones
- If the source material is in Spanish or another language, keep the flashcards in that language

Return ONLY valid JSON array:
[
  {{"front": "What is ...?", "back": "It is ..."}},
  ...
]

No markdown fences, no explanation. ONLY the JSON array."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        cards = json.loads(raw)
        if isinstance(cards, list):
            return [{"front": c.get("front", ""), "back": c.get("back", "")}
                    for c in cards if c.get("front") and c.get("back")]
        return []
    except Exception as e:
        log.error("Flashcard generation failed for %s: %s", course_name, e)
        return []


# ── Quiz generation ─────────────────────────────────────────

def generate_quiz(
    course_name: str,
    topics: list[str] | None = None,
    source_text: str = "",
    difficulty: str = "medium",
    count: int = 10,
) -> list[dict]:
    """
    Generate multiple-choice quiz questions from course material using AI.

    Returns:
        [{"question": "...", "option_a": "...", "option_b": "...",
          "option_c": "...", "option_d": "...", "correct": "a",
          "explanation": "..."}, ...]
    """
    context = ""
    if source_text:
        context = f"\n\nSOURCE MATERIAL:\n{source_text[:10000]}"
    topics_str = ", ".join(topics) if topics else "all key concepts"

    diff_desc = {
        "easy": "basic recall and definitions — suitable for initial review",
        "medium": "application and understanding — typical exam-level questions",
        "hard": "analysis, edge cases, and tricky scenarios — challenge-level"
    }.get(difficulty, "typical exam-level questions")

    prompt = f"""You are creating a practice quiz for the course "{course_name}".
Topics: {topics_str}
Difficulty: {difficulty} — {diff_desc}
{context}

Generate exactly {count} multiple-choice questions. Each question should:
- Be clear and unambiguous
- Have exactly 4 options (a, b, c, d) where only ONE is correct
- Include a brief explanation of why the correct answer is right
- Vary in the concepts tested
- If the source material is in Spanish or another language, keep the quiz in that language

Return ONLY valid JSON array:
[
  {{
    "question": "Which of the following ...?",
    "option_a": "First option",
    "option_b": "Second option",
    "option_c": "Third option",
    "option_d": "Fourth option",
    "correct": "b",
    "explanation": "Option B is correct because ..."
  }},
  ...
]

No markdown fences, no explanation. ONLY the JSON array."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=6000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        questions = json.loads(raw)
        if isinstance(questions, list):
            return [q for q in questions
                    if q.get("question") and q.get("correct") in ("a", "b", "c", "d")]
        return []
    except Exception as e:
        log.error("Quiz generation failed for %s: %s", course_name, e)
        return []


# ── Notes / Summary generation ──────────────────────────────

def generate_notes(
    course_name: str,
    topics: list[str] | None = None,
    source_text: str = "",
) -> dict:
    """
    Generate structured study notes from course material using AI.

    Returns:
        {"title": "...", "content_html": "<h2>...</h2><p>...</p>..."}
    """
    context = ""
    if source_text:
        context = f"\n\nSOURCE MATERIAL:\n{source_text[:12000]}"
    topics_str = ", ".join(topics) if topics else "all key concepts"

    prompt = f"""You are creating comprehensive study notes for the course "{course_name}".
Topics to cover: {topics_str}
{context}

Create well-structured study notes in HTML format. The notes should:
- Use <h2> for major sections and <h3> for subsections
- Use <ul>/<li> for lists of key points
- Use <strong> for important terms and definitions
- Use <p> for explanatory paragraphs
- For math equations, use LaTeX notation: inline math with $...$ and display math with $$...$$
- Example: <p>The derivative of $x^n$ is $nx^{{n-1}}$</p>
- Example display: <p>$$\\int_a^b f(x)\\,dx = F(b) - F(a)$$</p>
- Add a brief summary section at the end
- Be thorough but concise — focus on what a student needs to know for exams
- If the source material is in Spanish or another language, keep the notes in that language

Return ONLY valid JSON:
{{
  "title": "Notes: Topic Name",
  "content_html": "<h2>Section 1</h2><p>...</p>..."
}}

No markdown fences. ONLY the JSON object."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "title": data.get("title", f"Notes: {course_name}"),
            "content_html": data.get("content_html", ""),
        }
    except Exception as e:
        log.error("Notes generation failed for %s: %s", course_name, e)
        return {"title": f"Notes: {course_name}", "content_html": "<p>Generation failed. Please try again.</p>"}


# ── Practice Problems generation ────────────────────────────

def generate_practice_problems(
    course_name: str,
    topic: str = "",
    difficulty: str = "medium",
    count: int = 5,
    source_text: str = "",
) -> list[dict]:
    """
    Generate math/STEM practice problems with step-by-step solutions.

    Returns list of: {"problem": "...", "solution": "...", "answer": "..."}
    Uses LaTeX notation with $ delimiters for math expressions.
    """
    context = ""
    if source_text:
        context = f"\n\nSOURCE MATERIAL (use this to create relevant problems):\n{source_text[:8000]}"

    prompt = f"""You are creating {count} practice problems for "{course_name}".
Topic: {topic or "general course content"}
Difficulty: {difficulty}
{context}

Create {count} practice problems with DETAILED step-by-step solutions.

IMPORTANT RULES:
- Use LaTeX math notation wrapped in $ for inline math and $$ for display math
- For example: $\\int_0^1 x^2 dx$ or $$\\frac{{d}}{{dx}}[x^n] = nx^{{n-1}}$$
- Each problem should test a different concept or technique
- Solutions must show EVERY step clearly — students learn from the process
- If the source material is in Spanish, write problems and solutions in Spanish
- Include the final answer separately for quick checking
- Problems should be exam-level difficulty for the specified level

Return ONLY valid JSON array:
[
  {{
    "problem": "Calculate $\\\\int_0^\\\\infty e^{{-x}} dx$",
    "solution": "We evaluate using the definition of improper integrals:\\n$$\\\\int_0^\\\\infty e^{{-x}} dx = \\\\lim_{{b \\\\to \\\\infty}} \\\\int_0^b e^{{-x}} dx$$\\nStep 1: ...",
    "answer": "$1$"
  }}
]

No markdown fences. ONLY the JSON array."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        log.error("Practice problems generation failed for %s: %s", course_name, e)
        return []


# ── AI Study Chat (Tutor) ──────────────────────────────────

def chat_with_tutor(
    course_name: str,
    user_message: str,
    history: list[dict] | None = None,
    context_text: str = "",
) -> str:
    """
    AI Tutor chat — answers student questions using course material as RAG context.

    Parameters:
        course_name:   Name of the course
        user_message:  The student's question
        history:       Recent chat messages [{"role": "user"/"assistant", "content": "..."}]
        context_text:  Relevant course notes/syllabus text for grounding

    Returns:
        The tutor's reply as a string.
    """
    system = f"""You are a friendly, expert AI tutor for the course "{course_name}".
Your role:
- Answer the student's questions clearly and helpfully
- Use the provided course material to give accurate, relevant answers
- If you don't know or the material doesn't cover it, say so honestly
- Use examples when helpful
- Keep answers concise but thorough
- Match the language of the student (if they write in Spanish, reply in Spanish)
- Encourage the student and be supportive"""

    if context_text:
        system += f"\n\nCOURSE MATERIAL FOR REFERENCE:\n{context_text[:8000]}"

    messages = [{"role": "system", "content": system}]
    if history:
        for h in history[-10:]:
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4,
            max_tokens=2000,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error("Chat tutor failed for %s: %s", course_name, e)
        return "Sorry, I couldn't process your question right now. Please try again."


# ── YouTube transcript → notes ──────────────────────────────

def notes_from_transcript(
    video_title: str,
    transcript: str,
    course_name: str = "",
) -> dict:
    """
    Generate study notes from a YouTube video transcript.

    Returns:
        {"title": "...", "content_html": "..."}
    """
    course_ctx = f' for the course "{course_name}"' if course_name else ""

    prompt = f"""You are creating study notes from a YouTube video{course_ctx}.
Video title: "{video_title}"

TRANSCRIPT:
{transcript[:12000]}

Create comprehensive, well-structured study notes in HTML format:
- Use <h2> for major sections and <h3> for subsections
- Use <ul>/<li> for key points
- Use <strong> for important terms
- Extract all key concepts, formulas, and examples
- Organize chronologically or by topic (whichever works better)
- Keep in the same language as the transcript

Return ONLY valid JSON:
{{
  "title": "Notes: Video Title",
  "content_html": "<h2>...</h2><p>...</p>"
}}

No markdown fences. ONLY the JSON object."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "title": data.get("title", f"Notes: {video_title}"),
            "content_html": data.get("content_html", ""),
        }
    except Exception as e:
        log.error("YouTube notes generation failed: %s", e)
        return {"title": f"Notes: {video_title}",
                "content_html": "<p>Failed to generate notes from transcript.</p>"}


def flashcards_from_transcript(
    video_title: str,
    transcript: str,
    count: int = 15,
) -> list[dict]:
    """Generate flashcards from a YouTube video transcript."""
    prompt = f"""Create {count} study flashcards from this YouTube video.
Video: "{video_title}"

TRANSCRIPT:
{transcript[:10000]}

Return ONLY a JSON array of flashcards:
[{{"front": "question", "back": "answer"}}]
Keep in the same language as the transcript. No markdown fences."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        cards = json.loads(raw)
        if isinstance(cards, list):
            return [{"front": c.get("front", ""), "back": c.get("back", "")}
                    for c in cards if c.get("front") and c.get("back")]
        return []
    except Exception as e:
        log.error("YouTube flashcards failed: %s", e)
        return []


# ── Weak topic detection ────────────────────────────────────

def detect_weak_topics(
    flashcard_data: list[dict],
    quiz_data: list[dict],
) -> dict:
    """
    Analyze flashcard accuracy and quiz scores to find weak topics
    and generate study recommendations.

    Returns:
        {"weak_topics": [...], "recommendations_html": "..."}
    """
    summary_lines = []
    for fd in flashcard_data:
        summary_lines.append(
            f"- Flashcard deck \"{fd['title']}\" (course: {fd.get('course_name','N/A')}): "
            f"{fd.get('accuracy', 0)}% accuracy ({fd.get('total_correct',0)}/{fd.get('total_seen',0)})"
        )
    for qd in quiz_data:
        summary_lines.append(
            f"- Quiz \"{qd['title']}\" (course: {qd.get('course_name','N/A')}): "
            f"best score {qd.get('best_score', 0)}% ({qd.get('attempts', 0)} attempts)"
        )

    if not summary_lines:
        return {"weak_topics": [], "recommendations_html":
                "<p>Not enough data yet. Complete some quizzes and review flashcards first!</p>"}

    prompt = f"""You are a study advisor. Analyze this student's performance data and identify weak topics.

PERFORMANCE DATA:
{chr(10).join(summary_lines)}

Return ONLY valid JSON:
{{
  "weak_topics": [
    {{"topic": "Topic name", "score": 45, "source": "quiz/flashcard", "course": "Course name"}}
  ],
  "recommendations_html": "<h3>Study Recommendations</h3><ul><li>Focus on ...</li></ul>"
}}

Sort weak_topics from weakest to strongest. Keep recommendations in the same language as the topic names."""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        log.error("Weak topic detection failed: %s", e)
        return {"weak_topics": [], "recommendations_html": "<p>Analysis failed. Try again.</p>"}
