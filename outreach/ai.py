"""
AI-powered email generation — uses OpenAI GPT to write personalized sequences.
"""
from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from outreach.config import OPENAI_API_KEY

log = logging.getLogger(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)


def generate_sequence(
    business_type: str,
    target_audience: str,
    tone: str = "professional",
    num_steps: int = 3,
) -> list[dict]:
    """Generate a full email sequence (initial + follow-ups) with A/B subject variants.

    Returns list of dicts:
      [{"step": 1, "subject_a": ..., "subject_b": ..., "body": ..., "delay_days": 0}, ...]
    """
    prompt = f"""You are an expert cold email copywriter. Generate a {num_steps}-step outreach sequence.

Business type: {business_type}
Target audience: {target_audience}
Tone: {tone}

RULES:
1. Two subject line variants (A and B) for A/B testing
2. Use EXACTLY these placeholders (double curly braces): {{{{name}}}}, {{{{company}}}}, {{{{role}}}}
3. DO NOT invent other placeholders. DO NOT use [Your Name], [Company], etc.
4. Sign off with "Best,\\n{{{{sender_name}}}}" — never use [Your Name] or similar
5. Keep emails short (3-5 sentences for initial, 2-3 for follow-ups)
6. Each follow-up should reference the previous email naturally
7. Include a clear call to action in every email
8. Write naturally — don't say "I hope this message finds you well"
9. If using the role placeholder, write it naturally: "as {{{{role}}}} at {{{{company}}}}" not "your role as {{{{role}}}}"

Return ONLY a JSON array with this exact structure:
[
  {{
    "step": 1,
    "subject_a": "Subject line variant A",
    "subject_b": "Subject line variant B",
    "body": "Hi {{{{name}}}},\\n\\nBody text mentioning {{{{company}}}} naturally.\\n\\nBest,\\n{{{{sender_name}}}}",
    "delay_days": 0
  }},
  {{
    "step": 2,
    "subject_a": "Follow-up subject A",
    "subject_b": "Follow-up subject B",
    "body": "Follow-up body",
    "delay_days": 3
  }}
]

No markdown, no explanation — just the JSON array."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=2000,
    )

    import json
    text = response.choices[0].message.content.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


def personalize_email(body: str, contact: dict, sender_name: str = "") -> str:
    """Replace placeholders with actual contact data."""
    import re
    result = body

    name = contact.get("name", "there")
    company = contact.get("company", "your company")
    role = contact.get("role", "")

    # Replace double-brace placeholders FIRST, then single-brace
    for old, new in [
        ("{{name}}", name), ("{{company}}", company),
        ("{{role}}", role), ("{{sender_name}}", sender_name),
        ("{name}", name), ("{company}", company),
        ("{role}", role), ("{sender_name}", sender_name),
    ]:
        result = result.replace(old, new)

    # Catch AI hallucinations: [Your Name], [Name], {Your Name}, etc.
    result = re.sub(r'\[Your Name\]', sender_name, result, flags=re.IGNORECASE)
    result = re.sub(r'\{Your Name\}', sender_name, result, flags=re.IGNORECASE)
    result = re.sub(r'\[Sender Name\]', sender_name, result, flags=re.IGNORECASE)
    result = re.sub(r'\[Company Name\]', company, result, flags=re.IGNORECASE)

    # Clean up empty role references: "as  at" → "at", "your role as " → ""
    result = re.sub(r'\s+as\s+at\s+', ' at ', result)
    result = re.sub(r'your role as\s*[,.]?', '', result, flags=re.IGNORECASE)
    result = re.sub(r'as\s*\{\}', '', result)

    return result


def personalize_subject(subject: str, contact: dict, sender_name: str = "") -> str:
    """Replace placeholders in subject line."""
    return personalize_email(subject, contact, sender_name)


def translate_email(subject: str, body: str, language: str) -> tuple[str, str]:
    """Translate a personalized email to the target language using GPT.
    Returns (translated_subject, translated_body)."""
    if not language or language.lower() in ("en", "english"):
        return subject, body

    prompt = f"""Translate the following email to {language}. Keep the same tone and formatting.
Do NOT translate proper names (people, companies). Keep line breaks.

SUBJECT: {subject}

BODY:
{body}

Return ONLY in this exact format:
SUBJECT: <translated subject>

BODY:
<translated body>"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    text = response.choices[0].message.content.strip()

    # Parse response
    try:
        subj_line = text.split("SUBJECT:", 1)[1].split("\n", 1)[0].strip()
        body_part = text.split("BODY:", 1)[1].strip()
        return subj_line, body_part
    except (IndexError, ValueError):
        return subject, body  # fallback to original if parsing fails


def generate_reply_draft(
    original_subject: str,
    original_body: str,
    reply_body: str,
    reply_sentiment: str,
    contact_name: str,
    contact_company: str,
    sender_name: str,
    business_context: str = "",
) -> str:
    """Generate an AI reply draft based on the original email thread and the contact's reply."""
    sentiment_guidance = {
        "positive": "The contact is interested. Write a warm, enthusiastic reply that moves toward scheduling a call or next step. Include a specific CTA like suggesting a time to chat.",
        "negative": "The contact is not interested. Write a graceful, short exit reply. Thank them for their time, leave the door open, and don't push.",
        "neutral": "The contact is ambiguous or asked a question. Write a helpful reply that answers likely questions and gently steers toward a meeting or call.",
    }
    guidance = sentiment_guidance.get(reply_sentiment, sentiment_guidance["neutral"])

    prompt = f"""You are replying to a cold email response. Write a professional reply.

CONTEXT:
- Contact: {contact_name} at {contact_company}
- Original subject: {original_subject}
- Your original email: {original_body[:500]}
- Their reply: {reply_body[:1000]}
- Business context: {business_context or 'Not specified'}

INSTRUCTIONS:
{guidance}

RULES:
1. Keep it short (2-4 sentences max)
2. Be natural and conversational, not salesy
3. Reference something specific from their reply
4. Sign off appropriately in the same language as their reply. For English use "Best,", for Spanish use "Saludos,", for French use "Cordialement,", etc. Always include {sender_name} after the sign-off.
5. Do NOT include a subject line — just the body

Return ONLY the reply body text, nothing else."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()


def _get_mail_reply_draft(
    from_name: str,
    from_email: str,
    subject: str,
    body: str,
    priority: str,
    category: str,
    sender_name: str,
    contact_context: dict | None = None,
) -> str:
    """Generate an AI reply draft for a Mail Hub email (general inbox, not cold outreach).
    If contact_context is provided, the AI tailors the reply to the relationship."""

    # Build contact context block
    contact_block = ""
    if contact_context:
        parts = []
        if contact_context.get("relationship"):
            parts.append(f"Relationship: {contact_context['relationship']}")
        if contact_context.get("company"):
            parts.append(f"Company: {contact_context['company']}")
        if contact_context.get("role"):
            parts.append(f"Role: {contact_context['role']}")
        if contact_context.get("notes"):
            parts.append(f"Notes about them: {contact_context['notes'][:500]}")
        if contact_context.get("personality"):
            parts.append(f"Their communication style: {contact_context['personality'][:300]}")
        if parts:
            contact_block = "\n\nCONTACT CONTEXT (use this to personalize your reply):\n" + "\n".join(f"- {p}" for p in parts)

    prompt = f"""You are writing a reply to an email in my inbox. Write a professional, helpful reply.

FROM: {from_name} <{from_email}>
SUBJECT: {subject}
PRIORITY: {priority}
CATEGORY: {category}

EMAIL BODY:
{body[:1500]}{contact_block}

INSTRUCTIONS:
- Write a direct, professional reply that addresses the email content
- If it's a newsletter or automated email, write a brief acknowledgment or unsubscribe request
- If it requires action, confirm you'll take care of it
- If it's a meeting request, confirm availability or ask for alternatives
- Keep it concise (2-5 sentences)
- Match the formality of the original email
{f'- IMPORTANT: Adapt your tone and style to match their communication preferences: {contact_context["personality"]}' if contact_context and contact_context.get("personality") else ""}
{f'- They are a {contact_context["relationship"]} — adjust formality accordingly' if contact_context and contact_context.get("relationship") else ""}
- Sign off appropriately in the same language as the original email. For English use "Best,", for Spanish "Saludos,", for French "Cordialement,", etc. Always include {sender_name} after the sign-off.

Return ONLY the reply body text, nothing else."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()


def get_optimal_send_hour(send_time_data: list[dict]) -> dict:
    """Analyze historical send-time open rates and return optimal sending windows.

    Returns dict with:
      best_hours: list of (hour, open_rate) tuples sorted by performance
      best_days: list of (day_name, open_rate) tuples sorted by performance
      recommendation: human-readable string
    """
    if not send_time_data:
        return {
            "best_hours": [],
            "best_days": [],
            "recommendation": "Not enough data yet. Send more emails to get optimization insights.",
        }

    from collections import defaultdict
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    # Aggregate by hour
    hour_stats = defaultdict(lambda: {"total": 0, "opens": 0})
    day_stats = defaultdict(lambda: {"total": 0, "opens": 0})

    for r in send_time_data:
        h = int(r["hour"])
        d = int(r["dow"])
        hour_stats[h]["total"] += r["total"]
        hour_stats[h]["opens"] += r["opens"]
        day_stats[d]["total"] += r["total"]
        day_stats[d]["opens"] += r["opens"]

    # Sort by open rate (min 3 sends to be considered)
    best_hours = []
    for h, s in hour_stats.items():
        if s["total"] >= 3:
            rate = s["opens"] / s["total"] * 100
            best_hours.append((h, round(rate, 1)))
    best_hours.sort(key=lambda x: -x[1])

    best_days = []
    for d, s in day_stats.items():
        if s["total"] >= 3:
            rate = s["opens"] / s["total"] * 100
            best_days.append((day_names[d], round(rate, 1)))
    best_days.sort(key=lambda x: -x[1])

    # Build recommendation
    rec_parts = []
    if best_hours:
        top_hours = best_hours[:3]
        hours_str = ", ".join(f"{h}:00 ({r}%)" for h, r in top_hours)
        rec_parts.append(f"Best hours: {hours_str}")
    if best_days:
        top_days = best_days[:3]
        days_str = ", ".join(f"{d} ({r}%)" for d, r in top_days)
        rec_parts.append(f"Best days: {days_str}")

    recommendation = ". ".join(rec_parts) if rec_parts else "Not enough data yet."

    return {
        "best_hours": best_hours[:5],
        "best_days": best_days,
        "recommendation": recommendation,
    }


# ── Subject Line Optimizer ───────────────────────────────────

SPAM_TRIGGERS = [
    "free", "guarantee", "guaranteed", "act now", "click here", "limited time",
    "risk-free", "no obligation", "winner", "cash", "prize", "earn money",
    "make money", "double your", "100%", "100 percent", "%%", "$$$", "!!", "!!!",
    "urgent", "asap", "order now", "buy now", "sale", "discount", "cheap",
    "amazing", "incredible", "best price", "unbelievable", "miracle",
]


def optimize_subject_line(
    subject: str,
    body_preview: str = "",
    audience: str = "",
    goal: str = "open",  # "open" | "reply" | "book_meeting"
) -> dict:
    """
    Score a subject line and suggest improvements.

    Returns:
        {
          "score": 0-100,
          "predicted_open_rate": "35-45%",
          "length_score": ..., "personalization_score": ..., "urgency_score": ...,
          "spam_risk": 0-100,
          "spam_triggers": ["free", "guarantee"],
          "issues": [...],
          "strengths": [...],
          "suggestions": ["subject A", "subject B", "subject C"],
          "analysis": "..."
        }
    """
    subject_lower = (subject or "").lower()
    triggers = [w for w in SPAM_TRIGGERS if w in subject_lower]
    length = len(subject or "")
    has_personalization = "{{name}}" in subject or "{{company}}" in subject or "{{role}}" in subject
    has_question = "?" in subject
    excessive_caps = sum(1 for c in subject if c.isupper()) > max(4, len(subject) * 0.35) if subject else False

    prompt = f"""You are a cold-email deliverability expert. Score this subject line.

Subject: "{subject}"
Audience: {audience or 'general B2B'}
Goal: {goal}
Body preview (first 200 chars): {body_preview[:200]}

Heuristic signals already detected:
- Length: {length} chars ({'ideal' if 30 <= length <= 55 else 'too short' if length < 30 else 'too long'})
- Spam triggers found: {triggers or 'none'}
- Personalization placeholders: {'yes' if has_personalization else 'no'}
- Excessive caps: {excessive_caps}
- Ends with question: {has_question}

Return ONLY valid JSON:
{{
  "score": 82,
  "predicted_open_rate": "38-48%",
  "length_score": 90,
  "personalization_score": 70,
  "urgency_score": 60,
  "curiosity_score": 75,
  "spam_risk": 15,
  "spam_triggers": {json.dumps(triggers)},
  "issues": ["Specific weakness", "..."],
  "strengths": ["What works", "..."],
  "suggestions": [
    "Higher-performing rewrite 1 (use {{{{name}}}} or {{{{company}}}} when helpful)",
    "Different angle rewrite 2",
    "Shorter punchier rewrite 3"
  ],
  "analysis": "1-2 sentence summary of why this will or won't perform"
}}

Rules for suggestions:
- Keep under 55 characters when possible
- No spam trigger words
- Use placeholders {{{{name}}}}, {{{{company}}}}, {{{{role}}}} when personalization helps
- Natural, human, conversational — never salesy"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=900,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        data["length"] = length
        data.setdefault("spam_triggers", triggers)
        data.setdefault("suggestions", [])
        return data
    except Exception as e:
        log.error("Subject line optimize failed: %s", e)
        return {
            "score": 50, "predicted_open_rate": "—", "length_score": 0,
            "personalization_score": 0, "urgency_score": 0, "curiosity_score": 0,
            "spam_risk": len(triggers) * 15, "spam_triggers": triggers,
            "issues": ["Analysis unavailable — please try again."],
            "strengths": [], "suggestions": [], "analysis": "",
            "length": length,
        }


# ── Reply Intelligence ───────────────────────────────────────

def classify_reply(
    reply_body: str,
    original_subject: str = "",
    original_body: str = "",
) -> dict:
    """
    Classify an inbound email reply for intent, sentiment, urgency, and recommended action.

    Returns:
        {
          "intent": "interested" | "not_interested" | "needs_info" | "unsubscribe" | "out_of_office" | "wrong_person" | "neutral",
          "sentiment": "positive" | "neutral" | "negative",
          "urgency": "high" | "medium" | "low",
          "buying_signal": 0-100,
          "detected_objections": ["price", "timing", "authority", "need"],
          "mentions_meeting": bool,
          "meeting_time_hint": "...",
          "recommended_action": "Reply within 2 hours with a calendar link",
          "one_line_summary": "Interested but wants pricing first",
          "red_flags": [],
          "next_steps": ["Send case study", "..."]
        }
    """
    prompt = f"""You are a B2B sales reply analyzer. Classify this reply precisely.

Original subject: {original_subject}
Original email body (first 600 chars):
{(original_body or '')[:600]}

Reply body:
{(reply_body or '')[:2000]}

Return ONLY valid JSON with these exact keys:
{{
  "intent": "interested|not_interested|needs_info|unsubscribe|out_of_office|wrong_person|neutral",
  "sentiment": "positive|neutral|negative",
  "urgency": "high|medium|low",
  "buying_signal": 0,
  "detected_objections": [],
  "mentions_meeting": false,
  "meeting_time_hint": "",
  "recommended_action": "Concrete next step (1 sentence)",
  "one_line_summary": "Very concise takeaway",
  "red_flags": [],
  "next_steps": []
}}

Rules:
- buying_signal: 0-100, where 100 means ready to close the deal
- detected_objections: any of "price", "timing", "authority", "need", "trust", "existing_vendor"
- mentions_meeting: true if they ask to schedule, book, call, chat, demo, meet
- red_flags: things like "already using competitor", "budget frozen", "not the decision maker"
- If reply is automated (OOO, auto-reply), intent = "out_of_office"
- If reply asks to unsubscribe, stop, remove me → intent = "unsubscribe", urgency = "high"
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        data.setdefault("intent", "neutral")
        data.setdefault("sentiment", "neutral")
        data.setdefault("urgency", "medium")
        data.setdefault("buying_signal", 0)
        data.setdefault("detected_objections", [])
        data.setdefault("mentions_meeting", False)
        data.setdefault("meeting_time_hint", "")
        data.setdefault("recommended_action", "")
        data.setdefault("one_line_summary", "")
        data.setdefault("red_flags", [])
        data.setdefault("next_steps", [])
        return data
    except Exception as e:
        log.error("Reply classify failed: %s", e)
        return {
            "intent": "neutral", "sentiment": "neutral", "urgency": "medium",
            "buying_signal": 0, "detected_objections": [], "mentions_meeting": False,
            "meeting_time_hint": "", "recommended_action": "Analysis unavailable",
            "one_line_summary": "", "red_flags": [], "next_steps": [],
        }


# ── Deliverability / Content Analyzer ────────────────────────

def analyze_email_content(subject: str, body: str) -> dict:
    """
    Analyze an email's content for spam risk and deliverability signals.

    Pure heuristics (no AI call) so it is instant and free. The AI-assisted
    subject_optimizer can be used for deeper analysis.

    Returns:
        {
          "spam_score": 0-100 (higher = worse),
          "checks": [{"name": "...", "pass": bool, "severity": "high/med/low", "detail": "..."}],
          "word_count": int,
          "link_count": int,
          "image_count": int,
          "caps_ratio": 0-1,
          "exclamation_count": int,
          "has_unsubscribe": bool,
          "recommendations": [...]
        }
    """
    b = body or ""
    s = subject or ""
    combined = (s + " " + b).lower()

    checks: list[dict] = []
    score = 0
    recommendations: list[str] = []

    # Spam trigger words
    triggers = [w for w in SPAM_TRIGGERS if w in combined]
    if triggers:
        score += min(30, len(triggers) * 6)
        checks.append({
            "name": f"Spam trigger words ({len(triggers)})",
            "pass": False,
            "severity": "high" if len(triggers) >= 3 else "medium",
            "detail": ", ".join(triggers[:6]),
        })
        recommendations.append(f"Remove or replace spam triggers: {', '.join(triggers[:5])}")
    else:
        checks.append({"name": "Spam trigger words", "pass": True, "severity": "low", "detail": "No common triggers found"})

    # Caps ratio
    letters = [c for c in s + b if c.isalpha()]
    caps_ratio = (sum(1 for c in letters if c.isupper()) / len(letters)) if letters else 0
    if caps_ratio > 0.30:
        score += 15
        checks.append({"name": "Excessive capitals", "pass": False, "severity": "high",
                       "detail": f"{int(caps_ratio*100)}% of letters are uppercase"})
        recommendations.append("Reduce ALL-CAPS words — they look shouty and trigger spam filters.")
    else:
        checks.append({"name": "Capitalization", "pass": True, "severity": "low",
                       "detail": f"{int(caps_ratio*100)}% uppercase"})

    # Exclamation marks
    excl = s.count("!") + b.count("!")
    if excl > 3:
        score += min(10, excl * 2)
        checks.append({"name": "Exclamation marks", "pass": False, "severity": "medium",
                       "detail": f"{excl} exclamation marks"})
        recommendations.append("Limit exclamation marks — 0-1 per email is ideal.")
    else:
        checks.append({"name": "Exclamation marks", "pass": True, "severity": "low", "detail": str(excl)})

    # Link count
    links = re.findall(r"https?://\S+", b)
    link_count = len(links)
    if link_count > 3:
        score += (link_count - 3) * 5
        checks.append({"name": "Links", "pass": False, "severity": "medium",
                       "detail": f"{link_count} links (>3 hurts deliverability)"})
        recommendations.append("Reduce to 1-2 links max per email.")
    else:
        checks.append({"name": "Links", "pass": True, "severity": "low", "detail": f"{link_count} link(s)"})

    # Image count
    img_count = len(re.findall(r"<img", b, re.I)) + len(re.findall(r"!\[", b))
    if img_count > 2:
        score += 5
        checks.append({"name": "Images", "pass": False, "severity": "low",
                       "detail": f"{img_count} images — text-heavy emails deliver better"})
    else:
        checks.append({"name": "Images", "pass": True, "severity": "low", "detail": f"{img_count} image(s)"})

    # Word count
    words = len(re.findall(r"\w+", b))
    if words < 40:
        score += 8
        checks.append({"name": "Length", "pass": False, "severity": "low",
                       "detail": f"{words} words — too short, may seem template-y"})
        recommendations.append("Expand body to at least 50-120 words for cold outreach.")
    elif words > 200:
        score += 10
        checks.append({"name": "Length", "pass": False, "severity": "medium",
                       "detail": f"{words} words — cold emails >200 words drop reply rates"})
        recommendations.append("Trim to 80-150 words. Shorter = more replies in cold outreach.")
    else:
        checks.append({"name": "Length", "pass": True, "severity": "low", "detail": f"{words} words"})

    # Unsubscribe link
    has_unsub = bool(re.search(r"unsubscribe|opt.?out|stop.?receiving", combined))
    if not has_unsub:
        score += 8
        checks.append({"name": "Unsubscribe / opt-out", "pass": False, "severity": "medium",
                       "detail": "No opt-out mechanism detected"})
        recommendations.append("Add a clear unsubscribe footer — required by CAN-SPAM / GDPR.")
    else:
        checks.append({"name": "Unsubscribe / opt-out", "pass": True, "severity": "low", "detail": "Detected"})

    # Personalization
    has_personal = bool(re.search(r"\{\{\s*(name|first_name|company|role)\s*\}\}", s + b))
    if not has_personal:
        score += 5
        checks.append({"name": "Personalization", "pass": False, "severity": "low",
                       "detail": "No {{name}} / {{company}} placeholders found"})
        recommendations.append("Add {{name}} or {{company}} placeholders for higher reply rates.")
    else:
        checks.append({"name": "Personalization", "pass": True, "severity": "low",
                       "detail": "Placeholders detected"})

    # Subject length
    sl = len(s)
    if sl > 60:
        score += 8
        checks.append({"name": "Subject length", "pass": False, "severity": "medium",
                       "detail": f"{sl} chars — gets truncated on mobile"})
    elif sl < 10:
        score += 8
        checks.append({"name": "Subject length", "pass": False, "severity": "medium",
                       "detail": f"{sl} chars — too short to be interesting"})
    else:
        checks.append({"name": "Subject length", "pass": True, "severity": "low",
                       "detail": f"{sl} chars"})

    return {
        "spam_score": min(100, score),
        "deliverability_score": max(0, 100 - min(100, score)),
        "checks": checks,
        "word_count": words,
        "link_count": link_count,
        "image_count": img_count,
        "caps_ratio": round(caps_ratio, 3),
        "exclamation_count": excl,
        "has_unsubscribe": has_unsub,
        "recommendations": recommendations,
    }

