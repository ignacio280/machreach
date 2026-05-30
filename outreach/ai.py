"""
AI-powered email generation — uses OpenAI GPT to write personalized sequences.
"""
from __future__ import annotations

import logging

from openai import OpenAI

from outreach.config import OPENAI_API_KEY

log = logging.getLogger(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)




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








# ── Subject Line Optimizer ───────────────────────────────────

SPAM_TRIGGERS = [
    "free", "guarantee", "guaranteed", "act now", "click here", "limited time",
    "risk-free", "no obligation", "winner", "cash", "prize", "earn money",
    "make money", "double your", "100%", "100 percent", "%%", "$$$", "!!", "!!!",
    "urgent", "asap", "order now", "buy now", "sale", "discount", "cheap",
    "amazing", "incredible", "best price", "unbelievable", "miracle",
]




# ── Reply Intelligence ───────────────────────────────────────



# ── Deliverability / Content Analyzer ────────────────────────


