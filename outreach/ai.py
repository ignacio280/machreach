"""
AI-powered email generation — uses OpenAI GPT to write personalized sequences.
"""
from __future__ import annotations

from openai import OpenAI

from outreach.config import OPENAI_API_KEY

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
4. Sign off with "Best,\\n{sender_name}"
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
- Sign off with "Best,\\n{sender_name}"

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
