"""AI weekly study plan.

The deterministic planner could only see dates: it round-robined upcoming
exams into whatever slots were free. This module gives the model the three
things that actually decide what to study — how heavy the evaluation is
(ponderación), how close it is, and how hard its material looks — by feeding
it the text MachReach already extracted from every file the student uploaded
for each evaluation.

The model's answer is never trusted as-is. `build_ai_week` validates every
block against the student's own exams and their declared availability, and the
caller falls back to the deterministic plan whenever the model is unavailable
or answers with something unusable. A plan is only as good as its worst
block, so an invalid block is dropped rather than repaired by guesswork.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from typing import TypedDict

log = logging.getLogger(__name__)


class DaySlot(TypedDict):
    """One day of the week the model is asked to fill.

    Spelled out because the values are not all the same type: without it the
    dict reads as `dict[str, object]`, and `capacity` — which is arithmetic
    everywhere it is used — comes back as something you cannot compare or
    subtract.
    """

    date: str
    weekday: str
    capacity: int

# Plus-only feature: default to a strong general model and let an operator
# point at a better one without a deploy. PLANNER_AI_FALLBACK_MODEL is tried
# once if the first model is rejected (wrong name, no access for this key).
PLANNER_MODEL = os.getenv("PLANNER_AI_MODEL", "gpt-4o")
PLANNER_FALLBACK_MODEL = os.getenv("PLANNER_AI_FALLBACK_MODEL", "gpt-4o-mini")

# Material budget. Generous on purpose — the whole point is that the model
# reads the real material — but bounded so one enormous PDF cannot crowd out
# every other evaluation or blow the context window.
CHARS_PER_FILE = 8000
FILES_PER_EXAM = 6
TOTAL_CHAR_BUDGET = 120000

MIN_BLOCK_MINUTES = 20
MAX_BLOCK_MINUTES = 120
PHASES = ("Aprender", "Repasar", "Practicar")


def _excerpt(text: str, limit: int = CHARS_PER_FILE) -> str:
    """Collapse whitespace and keep the head of the document.

    Course material front-loads what matters (syllabus, unit list, rubric), so
    truncating the tail costs less than sampling the middle at random.
    """
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:limit]


def collect_exam_context(client_id: int, today: date, horizon_days: int = 60) -> list[dict]:
    """Every upcoming evaluation with its weight, distance and material."""
    from student import db as sdb

    budget = TOTAL_CHAR_BUDGET
    out: list[dict] = []
    for row in (sdb.get_upcoming_exams(client_id) or []):
        exam = dict(row)
        raw_date = str(exam.get("exam_date") or "")[:10]
        try:
            exam_day = date.fromisoformat(raw_date)
        except ValueError:
            continue
        days_left = (exam_day - today).days
        if days_left < 0 or days_left > horizon_days:
            continue
        course_id = int(exam.get("course_id") or 0)
        exam_id = int(exam.get("id") or 0)
        material = []
        try:
            files = sdb.get_course_files(client_id, course_id, exam_id) or []
        except Exception:
            files = []
        for handle in files[:FILES_PER_EXAM]:
            if budget <= 0:
                break
            text = _excerpt(handle.get("extracted_text") or "", min(CHARS_PER_FILE, budget))
            if not text:
                continue
            budget -= len(text)
            material.append({
                "file": handle.get("original_name") or "material",
                "chars": len(str(handle.get("extracted_text") or "")),
                "text": text,
            })
        out.append({
            "exam_id": exam_id,
            "course_id": course_id,
            "course": exam.get("course_name") or "Curso",
            "name": exam.get("name") or "Evaluación",
            "date": raw_date,
            "days_left": days_left,
            "weight_pct": int(exam.get("weight_pct") or 0),
            "material": material,
            "material_files": len(files),
        })
    return out


def _prompt(exams: list[dict], days: list[DaySlot], today: date) -> str:
    catalogue = []
    for exam in exams:
        entry = [
            f'- exam_id {exam["exam_id"]} · {exam["name"]} ({exam["course"]})',
            f'  fecha {exam["date"]} · faltan {exam["days_left"]} días · ponderación {exam["weight_pct"]}%',
        ]
        if exam["material"]:
            for handle in exam["material"]:
                entry.append(f'  --- material: {handle["file"]} ({handle["chars"]} caracteres) ---')
                entry.append(f'  {handle["text"]}')
        else:
            entry.append("  (sin material subido: infiere la dificultad del nombre y del curso)")
        catalogue.append("\n".join(entry))

    slots = "\n".join(
        f'- {day["date"]} ({day["weekday"]}): {day["capacity"]} minutos disponibles'
        + (" — DÍA LIBRE, no asignes bloques" if day["capacity"] <= 0 else "")
        for day in days
    )

    return f"""Eres el planificador de estudio de MachReach. Hoy es {today.isoformat()}.

Arma el plan de esta semana para un estudiante universitario. Decide qué estudiar
cada día combinando TRES señales, en este orden de importancia:
1. Ponderación: una evaluación que vale 40% merece mucho más tiempo que una de 10%.
2. Fecha: lo que se rinde antes va primero; lo lejano se siembra con bloques cortos.
3. Dificultad real del material: léelo. Si el material es denso, técnico, con
   demostraciones o mucho vocabulario nuevo, asigna más minutos y empieza antes.

Evaluaciones y su material:
{chr(10).join(catalogue)}

Disponibilidad de la semana (no la excedas nunca):
{slots}

Devuelve SOLO JSON con esta forma exacta:
{{
  "difficulty": [
    {{"exam_id": 1, "score": 4, "reason": "Demostraciones y series; el material asume álgebra lineal previa."}}
  ],
  "days": [
    {{"date": "YYYY-MM-DD", "blocks": [
      {{"exam_id": 1, "phase": "Aprender", "title": "Series de Taylor: criterio de convergencia",
        "topic": "Convergencia y radio", "minutes": 60,
        "why": "Vale 40%, se rinde en 6 días y el material dedica 3 secciones a esto."}}
    ]}}
  ]
}}

Reglas duras:
- Usa únicamente los exam_id listados arriba.
- "score" de dificultad es un entero de 1 (trivial) a 5 (muy difícil).
- "minutes" es múltiplo de 10, entre {MIN_BLOCK_MINUTES} y {MAX_BLOCK_MINUTES}.
- La suma de minutos de un día NO puede superar los minutos disponibles de ese día.
- Un día libre (0 minutos) va con "blocks": [].
- "phase" es exactamente una de: {", ".join(PHASES)}. Usa Aprender cuando falta
  entender, Repasar para consolidar, Practicar cuando la evaluación está cerca.
- "title" describe el contenido concreto sacado del material, no "estudiar para la prueba".
- "why" explica la decisión citando ponderación, fecha y/o dificultad del material.
- Incluye los 7 días en "days", en orden.
- Sin markdown, sin texto fuera del JSON."""


def _call_model(prompt: str) -> dict | None:
    from student.analyzer import _ai

    # This runs inside a web request, and gunicorn kills the whole worker —
    # all four threads — at 120s. Two model attempts at 40s each leave room
    # for the deterministic fallback to still answer the student in time.
    # No SDK retries: the fallback model IS the retry.
    client = _ai().with_options(timeout=40.0, max_retries=0)
    last_error: Exception | None = None
    for model in (PLANNER_MODEL, PLANNER_FALLBACK_MODEL):
        if not model:
            continue
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
                max_tokens=6000,
            )
            raw = (response.choices[0].message.content or "").strip()
            raw = re.sub(r"^```json?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            log.warning("Planner model %s returned invalid JSON: %s", model, exc)
        except Exception as exc:
            last_error = exc
            log.warning("Planner model %s failed: %s", model, exc)
    if last_error:
        log.error("AI planner unavailable, falling back: %s", last_error)
    return None


def build_ai_week(client_id: int, today: date, week_start: date, settings: dict,
                  plan_from: date | None = None) -> dict | None:
    """Return {"days": [...], "difficulty": {...}} or None to use the fallback.

    Blocks are validated against the student's own evaluations and their stated
    capacity: a hallucinated exam id, an over-long block or a day that busts
    its budget is dropped, never trusted.
    """
    # Urgency is measured from the real date; the planning window may start
    # later (a Sunday regeneration plans the week ahead).
    first_day = plan_from or today
    exams = collect_exam_context(client_id, today)
    if not exams:
        return None

    days: list[DaySlot] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        setting = settings.get(day.weekday()) or {}
        free = bool(setting.get("is_free_day"))
        capacity = 0 if free or day < first_day else int(round(float(setting.get("available_hours") or 0) * 60))
        days.append({
            "date": day.isoformat(),
            "weekday": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][day.weekday()],
            "capacity": max(0, capacity),
        })
    if not any(slot["capacity"] > 0 for slot in days):
        return None

    answer = _call_model(_prompt(exams, days, today))
    if not isinstance(answer, dict):
        return None

    by_id = {exam["exam_id"]: exam for exam in exams}
    difficulty: dict[int, dict] = {}
    for row in (answer.get("difficulty") or []):
        if not isinstance(row, dict):
            continue
        exam_id = int(row.get("exam_id") or 0)
        if exam_id not in by_id:
            continue
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            continue
        difficulty[exam_id] = {
            "score": min(5, max(1, score)),
            "reason": str(row.get("reason") or "")[:240],
        }

    capacity_by_date = {day["date"]: day["capacity"] for day in days}
    answered = {}
    for row in (answer.get("days") or []):
        if isinstance(row, dict) and str(row.get("date") or "") in capacity_by_date:
            answered[str(row["date"])] = row.get("blocks") or []

    out_days = []
    index = 0
    # `slot`, not `day`: the name is already a date earlier in this function,
    # and rebinding it to a dict here is what made the week unreadable to a
    # type checker — and to anyone tracing what `day` holds at a given line.
    for slot in days:
        remaining = slot["capacity"]
        blocks = []
        for raw in (answered.get(slot["date"]) or []):
            if not isinstance(raw, dict) or remaining < MIN_BLOCK_MINUTES:
                continue
            exam_id = int(raw.get("exam_id") or 0)
            exam = by_id.get(exam_id)
            if not exam:
                continue
            try:
                minutes = int(raw.get("minutes") or 0)
            except (TypeError, ValueError):
                continue
            minutes = min(MAX_BLOCK_MINUTES, max(MIN_BLOCK_MINUTES, minutes))
            minutes = min(minutes, remaining)
            if minutes < MIN_BLOCK_MINUTES:
                continue
            phase = str(raw.get("phase") or "").strip().capitalize()
            if phase not in PHASES:
                phase = "Repasar"
            title = str(raw.get("title") or "").strip()[:120] or f'{phase} para {exam["name"]}'
            blocks.append({
                "index": index,
                "phase": phase,
                "title": title,
                "copy": f'{exam["course"]} · {exam["date"][8:10]}/{exam["date"][5:7]}',
                "course": exam["course"],
                "course_id": exam["course_id"],
                "exam_id": exam_id,
                "minutes": minutes,
                "material_based": bool(exam["material"]),
                "topic": str(raw.get("topic") or "").strip()[:120],
                "why": str(raw.get("why") or "").strip()[:240],
                "difficulty": (difficulty.get(exam_id) or {}).get("score", 0),
            })
            index += 1
            remaining -= minutes
        out_days.append({
            "date": slot["date"],
            "hours": round(slot["capacity"] / 60, 2),
            "blocks": blocks,
        })

    if not index:
        return None
    return {"days": out_days, "difficulty": difficulty, "exams": exams, "model": PLANNER_MODEL}
