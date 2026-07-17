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
import random
import re
from datetime import date

from openai import OpenAI

from machreach_core.config import OPENAI_API_KEY

log = logging.getLogger(__name__)

_client: OpenAI | None = None


_LETTER_REF_RE = re.compile(
    r"\b((?:option|opci[oó]n|alternativa|letra|respuesta)\s+)([abcdABCD])\b"
)


def _remap_letters_in_text(text: str, mapping: dict[str, str]) -> str:
    """Rewrite references like 'opción B' in an explanation after reshuffling."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        old = m.group(2)
        new = mapping.get(old.lower(), old.lower())
        return m.group(1) + (new.upper() if old.isupper() else new)

    return _LETTER_REF_RE.sub(repl, text)


def _balance_quiz_answer_letters(questions: list[dict]) -> list[dict]:
    """Shuffle choices so correct answers do not cluster on one letter.

    Also remaps any 'option X' references inside the explanation so it keeps
    pointing at the right letter after the shuffle.
    """
    letters = ["a", "b", "c", "d"]
    target_cycle = letters[:]
    random.shuffle(target_cycle)
    out = []
    for idx, q in enumerate(questions or []):
        correct = (q.get("correct") or "").strip().lower()
        if correct not in letters:
            out.append(q)
            continue
        target = target_cycle[idx % len(target_cycle)]
        other_old = [k for k in letters if k != correct]
        other_new = [k for k in letters if k != target]
        random.shuffle(other_old)
        mapping = {correct: target}
        for old, new in zip(other_old, other_new):
            mapping[old] = new
        nq = dict(q)
        for old, new in mapping.items():
            nq[f"option_{new}"] = q.get(f"option_{old}", "")
        nq["correct"] = target
        nq["explanation"] = _remap_letters_in_text(q.get("explanation", ""), mapping)
        out.append(nq)
    return out


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
    calendar_events: str = "",
    date_overrides: list[dict] | None = None,
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

    # Build exam timeline + extract per-exam study materials so the AI plans
    # coverage of the WHOLE document, not just the topic list.
    all_exams = []
    materials_blocks = []  # one big text block per exam with full material
    # We strip "materials" out of courses_data before JSON-dumping so the dump
    # stays compact AND the AI gets the full text in a dedicated section.
    courses_for_summary = []
    for cd in courses_data:
        slim_course = {k: v for k, v in cd.items() if k != "exams"}
        slim_exams = []
        for exam in cd.get("exams", []):
            slim_exam = {
                "name": exam.get("name", "Exam"),
                "date": exam.get("date"),
                "weight_pct": exam.get("weight_pct", 0),
                "topics": exam.get("topics", []),
                "material_files": exam.get("material_files", []),
                "has_full_material": bool(exam.get("materials")),
            }
            slim_exams.append(slim_exam)
            all_exams.append({
                "course": cd["course_name"],
                "name": exam.get("name", "Exam"),
                "date": exam.get("date"),
                "weight_pct": exam.get("weight_pct", 0),
                "topics": exam.get("topics", []),
            })
            mat = (exam.get("materials") or "").strip()
            if mat:
                materials_blocks.append(
                    f"### {cd['course_name']} — {exam.get('name','Exam')} (date: {exam.get('date') or 'TBD'})\n"
                    f"Files: {', '.join(exam.get('material_files', [])) or '(unnamed)'}\n"
                    f"--- FULL MATERIAL BELOW ---\n{mat}\n--- END MATERIAL ---"
                )
        slim_course["exams"] = slim_exams
        courses_for_summary.append(slim_course)

    courses_summary = json.dumps(courses_for_summary, ensure_ascii=False, indent=2)[:30000]
    materials_section = "\n\n".join(materials_blocks)
    if materials_section:
        # NO cap — full materials are sent to the AI so every chapter is covered
        materials_ctx = (
            "\n\n=========================\n"
            "FULL STUDY MATERIALS PER EXAM (you MUST cover EVERY chapter, section, and topic appearing below — do NOT stop after chapter 1)\n"
            "=========================\n"
            + materials_section
        )
    else:
        materials_ctx = ""
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

    calendar_ctx = ""
    if calendar_events:
        calendar_ctx = (
            "\nGOOGLE CALENDAR EVENTS (real commitments — do NOT schedule study during these blocks):\n"
            + calendar_events
            + "\nWork around these. If a study block conflicts, move it earlier or to a free slot the same day."
        )

    overrides_ctx = ""
    if date_overrides:
        lines = []
        for o in date_overrides:
            d = o.get("date")
            if not d:
                continue
            if o.get("free"):
                lines.append(f"  - {d}: FREE DAY (override) — schedule 0 hours of study, no sessions")
            else:
                hrs = o.get("hours", 0)
                note = o.get("note", "")
                extra = f" ({note})" if note else ""
                lines.append(f"  - {d}: ONLY {hrs}h available (override){extra} — do NOT exceed this")
        if lines:
            overrides_ctx = (
                "\nPER-DATE AVAILABILITY OVERRIDES (these REPLACE the weekly default for those specific dates — they are HARD limits):\n"
                + "\n".join(lines)
                + "\nFor any date NOT listed here, use the weekly schedule above."
            )

    prompt = f"""You are an expert academic study planner. Today is {today}.

A student has these courses with their exam schedules and topics:

{courses_summary}

Student preferences: {prefs_str}

{schedule_ctx}
{diff_ctx}
{incomplete_ctx}
{calendar_ctx}
{overrides_ctx}
{materials_ctx}

Create a detailed daily study plan for the NEXT 14 DAYS ONLY (from {today}).
Return ONLY valid JSON:

{{
  "material_breakdown": [
    {{
      "course": "Course Name",
      "exam": "Midterm 1",
      "exam_date": "YYYY-MM-DD",
      "total_pages_estimate": 120,
      "segments": [
        {{"pages": "1-30", "estimated_hours": 2.5, "assigned_dates": ["YYYY-MM-DD"]}}
      ]
    }}
  ],
  "daily_plan": [
    {{
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "total_hours": 4,
      "sessions": [
        {{
          "course": "Course Name",
          "topic": "Read pages 1-30 (~30 pages)",
          "hours": 2.0,
          "type": "study",
          "priority": "high",
          "reason": "Exam in 5 days"
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
- ABSOLUTE HARD RULE — PAGE-BASED COVERAGE: For EVERY exam that has FULL STUDY MATERIALS attached above, each file lists either `TOTAL PAGES: X` (exact, from the PDF) or `ESTIMATED PAGES: X` (estimated from text length). Use that number as `material_breakdown.total_pages_estimate`. Then split the document into reading segments by page range and assign each segment to a study date in `material_breakdown.segments`. Every page from 1 to the last page MUST be covered by exactly one segment — no gaps, no overlaps. The sum of all segment page-ranges must equal the total page count.
- TOPIC LABELS — Each session's "topic" field must use a PAGE-BASED label only, NEVER chapter names. Required format examples: "Read pages 1-30 (~30 pages)", "Pages 31-55 (~25 pages)", "Final review pages 1-120". FORBIDDEN labels: "Chapter 1", "Capítulo 1", "Introducción", "Introduction", "Unit 2", "Unidad 2", "Solemne preparation", "Review chapter X", or any chapter / section / unit / lesson name. If you do not know the page count exactly, estimate it (~3000 chars per page) and STILL use page-range labels — never fall back to chapter names. The student must always see a page range, never a chapter title.
- COMPLETE COVERAGE: Distribute pages proportionally across the available study days so the student finishes 100% of the document at least 1 day before the exam. Aim for a steady reading pace (e.g. 20–40 pages per study hour depending on subject difficulty).
- HARD RULE — NEVER schedule study sessions for an exam AFTER its exam date. Once the exam date passes, that exam is DONE — do not include any further sessions for it. If the exam is on YYYY-MM-DD, your last session for that exam's material must be on YYYY-MM-DD or earlier (ideally the day BEFORE the exam for final review). Sessions on the exam date itself should be light review only, not new material.
- HARD RULE — Front-load study so all material is covered BEFORE the exam, not after. If you only have 6 days until the exam, compress the material into those 6 days.
- HARD RULE — The student must be FULLY PREPARED by the END of the day BEFORE the exam. If the exam is on day D, the day-D−1 plan must include a final FULL REVIEW session (type "review", priority "high") covering the entire material, and ALL new-material study must be completed by day D−1. The exam date itself (D) should have NO study unless explicitly a quick warm-up review.
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
                    return _post_process_plan(json.loads(raw + closer), courses_data, date_overrides)
                except json.JSONDecodeError:
                    continue
            raise ValueError("AI response was truncated and could not be repaired")

        return _post_process_plan(json.loads(raw), courses_data, date_overrides)
    except Exception as e:
        log.error("Study plan generation failed: %s", e)
        raise RuntimeError(f"Plan generation failed: {e}")


def _post_process_plan(plan: dict, courses_data: list[dict], date_overrides: list[dict] | None = None) -> dict:
    """Strip out sessions scheduled AFTER an exam's date — the AI sometimes
    forgets that an exam ends, and schedules continued study afterwards."""
    if not isinstance(plan, dict):
        return plan
    # Build {course_name -> {exam_name_lower -> exam_date_str}} index
    exam_dates = {}  # course_name -> last_exam_date (date)
    course_exam_dates = {}  # (course_name, exam_topic_keyword) -> date
    from datetime import date as _date
    for cd in courses_data or []:
        cname = cd.get("course_name")
        if not cname:
            continue
        latest = None
        for ex in cd.get("exams", []):
            ed = ex.get("date")
            if not ed:
                continue
            try:
                ed_obj = _date.fromisoformat(ed[:10])
            except Exception:
                continue
            if latest is None or ed_obj > latest:
                latest = ed_obj
        if latest:
            exam_dates[cname.lower().strip()] = latest
    # Build override index by ISO date string.
    overrides_by_date = {}
    for o in date_overrides or []:
        d = (o.get("date") or "")[:10]
        if not d:
            continue
        overrides_by_date[d] = {
            "free": bool(o.get("free")),
            "hours": float(o.get("hours") or 0),
            "note": o.get("note", ""),
        }
    if not exam_dates and not overrides_by_date:
        return plan
    new_daily = []
    for day in plan.get("daily_plan", []) or []:
        try:
            day_date = _date.fromisoformat((day.get("date") or "")[:10])
        except Exception:
            new_daily.append(day)
            continue
        # Apply date overrides — these are HARD limits.
        ov = (overrides_by_date.get(day_date.isoformat())
              if date_overrides else None)
        if ov and ov.get("free"):
            day["sessions"] = []
            day["total_hours"] = 0
            new_daily.append(day)
            continue
        kept_sessions = []
        for s in day.get("sessions", []) or []:
            cn = (s.get("course") or "").lower().strip()
            last_exam = exam_dates.get(cn)
            if last_exam and day_date > last_exam:
                # Skip — this study session is scheduled AFTER the course's last exam.
                continue
            kept_sessions.append(s)
        # If a date override caps hours, trim sessions proportionally from the END
        if ov and not ov.get("free"):
            cap = float(ov.get("hours") or 0)
            running = 0.0
            trimmed = []
            for s in kept_sessions:
                h = float(s.get("hours", 0) or 0)
                if running + h <= cap + 0.01:
                    trimmed.append(s)
                    running += h
                else:
                    remaining = max(0.0, cap - running)
                    if remaining >= 0.25:
                        s2 = dict(s)
                        s2["hours"] = round(remaining, 2)
                        trimmed.append(s2)
                        running = cap
                    break
            kept_sessions = trimmed
        day["sessions"] = kept_sessions
        # Recompute total_hours if it was set
        if "total_hours" in day:
            try:
                day["total_hours"] = round(sum(float(s.get("hours", 0) or 0) for s in kept_sessions), 2)
            except Exception:
                pass
        new_daily.append(day)
    plan["daily_plan"] = new_daily
    return plan


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
    For long documents, chunks the text and generates in parallel.

    Returns:
        [{"front": "question", "back": "answer"}, ...]
    """
    topics_str = ", ".join(topics) if topics else "all key concepts from the material"

    # For very long texts, chunk and distribute card counts across chunks
    MAX_CHARS_SINGLE = 60000  # safe single-call limit (~15K tokens)
    if source_text and len(source_text) > MAX_CHARS_SINGLE:
        from concurrent.futures import ThreadPoolExecutor
        chunks = _split_into_chunks(source_text, max_chars=MAX_CHARS_SINGLE, hard_cap_chunks=10)
        if len(chunks) > 1:
            cards_per_chunk = max(5, count // len(chunks))
            remainder = count - cards_per_chunk * len(chunks)
            targets = [cards_per_chunk + (1 if i < remainder else 0) for i in range(len(chunks))]
            log.info("Flashcard gen: %d chunks, %d total cards (course=%s)", len(chunks), count, course_name)

            def _gen_chunk_fc(args):
                label, body, n = args
                return generate_flashcards(
                    course_name=course_name, topics=topics,
                    source_text=body, count=n,
                )

            with ThreadPoolExecutor(max_workers=min(6, len(chunks))) as ex:
                results = list(ex.map(_gen_chunk_fc, [(l, b, t) for (l, b), t in zip(chunks, targets)]))
            all_cards = []
            seen = set()
            for batch in results:
                for c in batch:
                    key = c["front"].strip().lower()
                    if key not in seen:
                        seen.add(key)
                        all_cards.append(c)
            return all_cards[:count]

    context = ""
    if source_text:
        context = f"\n\nSTUDENT'S UPLOADED MATERIAL:\n{source_text}"

    prompt = f"""You are creating study flashcards for the course "{course_name}".
Topics to cover: {topics_str}
{context}

STRICT RULES:
- Create flashcards ONLY from the source material provided above
- Do NOT add information from outside the provided material
- Every question and answer must be directly based on the content above

Generate exactly {count} flashcards. Each flashcard should:
- Be high-difficulty: require explanation, comparison, edge cases, cause/effect, or application. Avoid trivial definition-only cards unless the concept is absolutely foundational.
- Have a clear, specific question on the front that would challenge a serious university student
- Have a concise but complete answer on the back, including the reasoning needed to avoid common traps
- Cover different aspects of the material
- Progress from basic concepts to more advanced ones
- For math equations, use LaTeX notation: inline with $...$ and display with $$...$$
- Keep the same language as the source material

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
            max_tokens=8000,
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

_QUIZ_BATCH_SIZE = 20  # keep each AI call below token limits


def _generate_quiz_batch(
    course_name: str,
    topics_str: str,
    difficulty: str,
    diff_desc: str,
    context: str,
    count: int,
    batch_idx: int,
    total_batches: int,
    existing_questions: list[str],
) -> list[dict]:
    """Generate one batch of quiz questions."""
    avoid_block = ""
    if existing_questions:
        # Show a trimmed sample so the model doesn't duplicate
        sample = existing_questions[-30:]
        avoid_block = (
            "\n\nAVOID DUPLICATING these questions already generated:\n- "
            + "\n- ".join(s[:120] for s in sample)
        )

    batch_note = ""
    if total_batches > 1:
        batch_note = (
            f"\nThis is batch {batch_idx + 1} of {total_batches}. "
            "Cover DIFFERENT sub-topics and angles than earlier batches."
        )

    prompt = f"""You are creating a practice quiz for the course "{course_name}".
Topics: {topics_str}
Difficulty: {difficulty} — {diff_desc}{batch_note}
{context}{avoid_block}

STRICT RULES:
- Create questions ONLY from the source material provided above
- Do NOT add information from outside the provided material
- Every question, option, and explanation must be directly based on the content above
- Make the quiz difficult, exam-quality, and conceptually demanding even when the requested difficulty says medium.
- Avoid obvious answers. Distractors must be plausible and based on common confusions from the material.
- Vary the correct answer letter naturally. Do not favor option B.

Generate exactly {count} multiple-choice questions. Each question must have:
- A short "topic" tag (2-5 words) naming the concept tested (e.g. "Photosynthesis", "Big-O notation", "French Revolution causes")
- Exactly 4 options (a, b, c, d) where only ONE is correct
- An explanation that genuinely TEACHES (2-4 sentences). It must:
  (1) state the underlying concept, definition, rule or principle from the material that decides the question;
  (2) show how that principle leads to the answer;
  (3) when useful, explain why the most tempting distractor is wrong (what confusion it plays on).
  NEVER write circular explanations that just restate the option text (BAD: "Es correcta porque la felicidad es un estado de plenitud" when the option already says that). The student should learn something new from reading it.
- IMPORTANT: in the explanation NEVER mention option letters (A, B, C or D) — options get reshuffled later. Refer to the answer by its content, e.g. "Es correcta porque la fotosíntesis ...", never "La opción B es correcta".
- Varied concepts across the batch
- For math equations, use LaTeX notation: inline with $...$ and display with $$...$$
- Keep the same language as the source material

Return ONLY a valid JSON array — no markdown fences, no commentary:
[
  {{
    "topic": "Short concept tag",
    "question": "Which of the following ...?",
    "option_a": "First option",
    "option_b": "Second option",
    "option_c": "Third option",
    "option_d": "Fourth option",
    "correct": "c",
    "explanation": "Según el material, [principio o definición que decide la pregunta]. Por eso [cómo ese principio lleva a la respuesta]. El error común es pensar que [por qué el distractor más tentador confunde], pero [aclaración]."
  }}
]"""

    try:
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
    except Exception:
        # Fallback without json_object (older / incompatible models)
        resp = _ai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content.strip()

    raw = re.sub(r"^```json?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage an array from within an object
        m = re.search(r"\[\s*\{.*\}\s*\]", raw, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return []

    # Accept either a bare list or an object wrapping a list
    if isinstance(parsed, dict):
        for key in ("questions", "quiz", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return []

    cleaned = []
    for q in parsed:
        if not isinstance(q, dict):
            continue
        if not q.get("question"):
            continue
        if q.get("correct") not in ("a", "b", "c", "d"):
            continue
        if not all(q.get(f"option_{k}") for k in ("a", "b", "c", "d")):
            continue
        cleaned.append({
            "topic": (q.get("topic") or "General").strip()[:80],
            "question": q["question"],
            "option_a": q["option_a"],
            "option_b": q["option_b"],
            "option_c": q["option_c"],
            "option_d": q["option_d"],
            "correct": q["correct"],
            "explanation": q.get("explanation", ""),
        })
    return cleaned


def generate_quiz(
    course_name: str,
    topics: list[str] | None = None,
    source_text: str = "",
    difficulty: str = "medium",
    count: int = 10,
) -> list[dict]:
    """
    Generate multiple-choice quiz questions from course material using AI.
    For long documents, chunks the text and generates in parallel.
    """
    try:
        count = int(count)
    except Exception:
        count = 10
    count = max(1, min(count, 100))  # hard safety ceiling

    # For very long texts, chunk and distribute question counts
    MAX_CHARS_SINGLE = 60000
    if source_text and len(source_text) > MAX_CHARS_SINGLE:
        from concurrent.futures import ThreadPoolExecutor
        chunks = _split_into_chunks(source_text, max_chars=MAX_CHARS_SINGLE, hard_cap_chunks=10)
        if len(chunks) > 1:
            qs_per_chunk = max(3, count // len(chunks))
            remainder = count - qs_per_chunk * len(chunks)
            targets = [qs_per_chunk + (1 if i < remainder else 0) for i in range(len(chunks))]
            log.info("Quiz gen: %d chunks, %d total questions (course=%s)", len(chunks), count, course_name)

            def _gen_chunk_qz(args):
                label, body, n = args
                return generate_quiz(
                    course_name=course_name, topics=topics,
                    source_text=body, difficulty=difficulty, count=n,
                )

            with ThreadPoolExecutor(max_workers=min(6, len(chunks))) as ex:
                results = list(ex.map(_gen_chunk_qz, [(l, b, t) for (l, b), t in zip(chunks, targets)]))
            all_qs = []
            seen = set()
            for batch in results:
                for q in batch:
                    key = q["question"].strip().lower()
                    if key not in seen:
                        seen.add(key)
                        all_qs.append(q)
            return _balance_quiz_answer_letters(all_qs[:count])

    context = f"\n\nSTUDENT'S UPLOADED MATERIAL:\n{source_text}" if source_text else ""
    topics_str = ", ".join(topics) if topics else "all key concepts from the material"
    diff_desc = {
        "easy": "basic recall and definitions — suitable for initial review",
        "medium": "application and understanding — typical exam-level questions",
        "hard": "analysis, edge cases, and tricky scenarios — challenge-level",
    }.get(difficulty, "typical exam-level questions")

    # Plan batches
    total_batches = (count + _QUIZ_BATCH_SIZE - 1) // _QUIZ_BATCH_SIZE
    all_questions: list[dict] = []
    seen_questions: list[str] = []

    remaining = count
    for batch_idx in range(total_batches):
        batch_target = min(_QUIZ_BATCH_SIZE, remaining)
        if batch_target <= 0:
            break
        try:
            batch = _generate_quiz_batch(
                course_name=course_name,
                topics_str=topics_str,
                difficulty=difficulty,
                diff_desc=diff_desc,
                context=context,
                count=batch_target,
                batch_idx=batch_idx,
                total_batches=total_batches,
                existing_questions=seen_questions,
            )
        except Exception as e:
            log.error("Quiz batch %d failed for %s: %s", batch_idx, course_name, e)
            batch = []
        # Dedup by question text
        existing_set = {q["question"].strip().lower() for q in all_questions}
        for q in batch:
            key = q["question"].strip().lower()
            if key in existing_set:
                continue
            existing_set.add(key)
            all_questions.append(q)
            seen_questions.append(q["question"])
        remaining = count - len(all_questions)

    # One retry to top up if the model under-delivered
    if len(all_questions) < count:
        try:
            short = count - len(all_questions)
            top_up = _generate_quiz_batch(
                course_name=course_name,
                topics_str=topics_str,
                difficulty=difficulty,
                diff_desc=diff_desc,
                context=context,
                count=min(short, _QUIZ_BATCH_SIZE),
                batch_idx=total_batches,
                total_batches=total_batches + 1,
                existing_questions=seen_questions,
            )
            existing_set = {q["question"].strip().lower() for q in all_questions}
            for q in top_up:
                key = q["question"].strip().lower()
                if key in existing_set:
                    continue
                existing_set.add(key)
                all_questions.append(q)
                if len(all_questions) >= count:
                    break
        except Exception as e:
            log.warning("Quiz top-up failed for %s: %s", course_name, e)

    return _balance_quiz_answer_letters(all_questions[:count])




# ── Shared chunking utility ─────────────────────────────────

def _split_into_chunks(txt: str, max_chars: int = 18000, hard_cap_chunks: int = 12) -> list[tuple[str, str]]:
    """Returns list of (chunk_label, chunk_text). Splits on Chapter/Capítulo/Section headings.

    Caps total chunks at `hard_cap_chunks` to keep total AI time bounded
    (each chunk ≈ 30-60s; even with parallelism we want a sane upper bound).
    """
    if not txt:
        return []
    heading_re = re.compile(
        r'^\s*(?:Chapter|Cap[ií]tulo|Unit|Unidad|Tema|Lecci[oó]n|Lesson|Part(?:e)?)\s+[\dIVXLC]+\b[^\n]*$',
        re.MULTILINE | re.IGNORECASE,
    )
    positions = [(m.start(), m.group().strip()) for m in heading_re.finditer(txt)]
    chunks: list[tuple[str, str]] = []
    if positions and len(positions) >= 2:
        for i, (pos, label) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(txt)
            body = txt[pos:end].strip()
            if len(body) > max_chars:
                for j in range(0, len(body), max_chars):
                    sub = body[j:j + max_chars]
                    chunks.append((f"{label} (part {j // max_chars + 1})", sub))
            elif body:
                chunks.append((label, body))
    else:
        for j in range(0, len(txt), max_chars):
            chunks.append((f"Part {j // max_chars + 1}", txt[j:j + max_chars]))

    if len(chunks) > hard_cap_chunks:
        target = hard_cap_chunks
        group_size = (len(chunks) + target - 1) // target
        merged: list[tuple[str, str]] = []
        for i in range(0, len(chunks), group_size):
            grp = chunks[i:i + group_size]
            label = f"{grp[0][0]} → {grp[-1][0]}" if len(grp) > 1 else grp[0][0]
            body = "\n\n".join(b for _, b in grp)
            merged.append((label, body))
        chunks = merged
    return chunks


# ── Notes / Summary generation ──────────────────────────────



# ── Practice Problems generation ────────────────────────────



# ── AI Study Chat (Tutor) ──────────────────────────────────



# ── YouTube transcript → notes ──────────────────────────────





# ── Homework Helper ─────────────────────────────────────────



# ── Essay / Writing Assistant ───────────────────────────────



# ── Panic Mode / Cram Plan ──────────────────────────────────
