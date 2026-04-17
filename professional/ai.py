"""
Pro AI Assistant — text polish, meeting agenda, LinkedIn post, cold-call script,
proposal outline. Reuses the OpenAI client from outreach.ai.
"""
from __future__ import annotations

import logging

from outreach.ai import client as _oai

log = logging.getLogger(__name__)
MODEL = "gpt-4o-mini"


def _chat(system: str, user: str, max_tokens: int = 700, temperature: float = 0.7) -> str:
    try:
        resp = _oai.chat.completions.create(
            model=MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("Pro AI error: %s", e)
        return f"(AI error: {e})"


def polish_text(text: str, tone: str = "professional") -> str:
    tones = {
        "professional": "Polished, confident, professional business tone.",
        "friendly": "Warm, friendly, conversational but still professional.",
        "concise": "As short and direct as possible. Remove fluff.",
        "persuasive": "Persuasive, benefit-focused, with a clear call to action.",
        "formal": "Formal, respectful, and highly polite.",
    }
    style = tones.get(tone, tones["professional"])
    return _chat(
        f"You are an expert business writing editor. Rewrite the user's text. {style} "
        f"Keep the same core meaning. Fix grammar and clarity. Return only the rewritten text.",
        text,
    )


def shorten_text(text: str, target_words: int = 50) -> str:
    return _chat(
        "You are an editor who shortens business text while keeping all key points and action items. "
        f"Aim for about {target_words} words. Return only the shortened text.",
        text,
    )


def meeting_agenda(topic: str, duration_min: int = 30, context: str = "") -> str:
    return _chat(
        "You create clear, actionable meeting agendas for professionals. "
        "Output plain text with: title, objective, timed sections, participants-needed, pre-reads, "
        "and a 'Decisions to make' list. No emojis.",
        f"Topic: {topic}\nDuration: {duration_min} minutes\nContext: {context or 'n/a'}",
        max_tokens=800,
    )


def linkedin_post(topic: str, key_points: str = "", audience: str = "professionals") -> str:
    return _chat(
        "You write high-performing LinkedIn posts for professionals. "
        "Use a strong hook in line 1, short lines, white space, and a clear takeaway. "
        "No hashtags salad — max 3 relevant hashtags at the end. Return only the post.",
        f"Topic: {topic}\nKey points: {key_points or 'Your choice'}\nAudience: {audience}",
        max_tokens=600,
    )


def cold_call_script(offer: str, target: str = "decision-maker", objection_handling: bool = True) -> str:
    extra = " Include a short section on the 3 most likely objections and how to handle each." \
        if objection_handling else ""
    return _chat(
        "You are a top B2B sales trainer. Write a concise cold-call script: "
        "opener, permission to continue, value proposition, discovery question, call-to-action." + extra,
        f"Offer: {offer}\nTarget: {target}",
        max_tokens=900,
    )


def proposal_outline(project: str, deliverables: str, budget: str = "") -> str:
    return _chat(
        "You write professional proposal outlines. Output sections: "
        "Executive Summary, Scope, Deliverables, Timeline, Investment, Terms, Next Steps.",
        f"Project: {project}\nDeliverables: {deliverables}\nBudget: {budget or 'TBD'}",
        max_tokens=1000,
    )


def brainstorm(prompt: str) -> str:
    return _chat(
        "You are a senior strategy advisor. Give 8-12 concrete, non-obvious ideas. "
        "Number each idea. One line per idea. No fluff.",
        prompt,
        max_tokens=800,
    )


def summarize_notes(notes: str) -> str:
    return _chat(
        "Summarize these meeting or call notes. Output: "
        "1) TL;DR (2 sentences). 2) Decisions made. 3) Action items with owner if mentioned. "
        "4) Open questions. Plain text.",
        notes,
        max_tokens=900,
    )


def generate_budget_plan(income: float, savings_goal: float, currency: str,
                        preferences: str, spending_summary: dict) -> str:
    """AI-generated personalized monthly budget based on real spending data."""
    by_cat_str = "\n".join(
        f"- {r['category'].replace('_',' ').title()}: {currency} {float(r['total']):,.2f}"
        for r in (spending_summary.get('by_category') or [])[:10]
    ) or "- (no recent transactions)"
    prompt = f'''User monthly income: {currency} {income:,.2f}
Savings goal: {currency} {savings_goal:,.2f}/month
Preferences / constraints: {preferences or "none stated"}

Recent 30-day spending by category:
{by_cat_str}
Total spent last 30 days: {currency} {spending_summary.get('total_spent', 0):,.2f}
Total income last 30 days: {currency} {spending_summary.get('total_income', 0):,.2f}'''
    return _chat(
        "You are a sharp but friendly personal finance coach. "
        "Build a concrete monthly budget based on the user's real spending data. Output:\n"
        "1) One-paragraph assessment (honest but not harsh).\n"
        "2) Recommended budget table: category -> suggested monthly cap (in the user's currency).\n"
        "3) 3-5 specific cuts to make (cite their actual data).\n"
        "4) Savings plan: how much to move to savings each week to hit their goal.\n"
        "5) Two warnings about their biggest risks.\n"
        "Plain text, no markdown headers, no emojis.",
        prompt, max_tokens=1200, temperature=0.4,
    )


def categorize_transaction(merchant: str, description: str = "") -> str:
    """Classify a transaction merchant into one of our standard categories."""
    cats = ("food_dining, groceries, transportation, shopping, entertainment, "
            "subscriptions, utilities, rent_mortgage, health, travel, education, "
            "business, income, transfer, other")
    out = _chat(
        f"Classify the transaction into EXACTLY one of these categories: {cats}. "
        "Return ONLY the category key, nothing else.",
        f"Merchant: {merchant}\nDescription: {description}",
        max_tokens=20, temperature=0,
    ).strip().lower().split()[0] if merchant else "other"
    valid = {"food_dining","groceries","transportation","shopping","entertainment",
             "subscriptions","utilities","rent_mortgage","health","travel","education",
             "business","income","transfer","other"}
    return out if out in valid else "other"


def relationship_summary(contact_name: str, contact_email: str, emails: list) -> str:
    """Build a 'second brain' summary of a contact's communication history."""
    if not emails:
        return "No email history yet for this contact."
    history = []
    for e in emails[:20]:
        dt = str(e.get("received_at",""))[:10]
        subj = (e.get("subject") or "").strip()[:120]
        prev = (e.get("body_preview") or "").strip().replace("\n", " ")[:200]
        history.append(f"[{dt}] {subj}\n  {prev}")
    hist_str = "\n\n".join(history)
    prompt = f'''Contact: {contact_name or contact_email} <{contact_email}>

Recent email history (most recent first):
{hist_str}'''
    return _chat(
        "You are a professional 'second brain' for the user. Summarize this contact. Output plain text:\n"
        "Contact Summary\n<name> (<role/company if inferrable, else 'professional contact'>)\n\n"
        "Last interaction: <when + topic>\n\n"
        "Key notes from conversations:\n- bullet 1\n- bullet 2\n- bullet 3\n\n"
        "Suggested response angle:\n<one sentence of concrete advice for the next message>\n\n"
        "Follow-ups promised or expected:\n- or 'None found'\n\n"
        "No markdown, no emojis. Be specific and helpful.",
        prompt, max_tokens=700, temperature=0.4,
    )


def suggest_contacts_for_goal(goal: str, contacts: list) -> str:
    """Given a user goal, recommend contacts from their network + draft a message."""
    if not contacts:
        return ("I can't make a recommendation because your network is empty.\n\n"
                "To enable this feature, either:\n"
                "- Connect your email inbox in Mail Hub so I can see who you've been talking to, or\n"
                "- Import your contacts via the Contacts page.\n\n"
                "Once you have contacts, come back and I'll tell you exactly who to reach out to for: \""
                + (goal[:200]) + "\".")
    lines = []
    for c in contacts[:40]:
        name = c.get("name") or c.get("email","")
        role = c.get("role") or ""
        company = c.get("company") or ""
        summary = (c.get("ai_summary") or "").replace("\n"," ")[:180]
        last = str(c.get("last_at","") or "")[:10]
        lines.append(f"- {name} <{c.get('email','')}> | {role} @ {company} | last: {last} | {summary}")
    network = "\n".join(lines)
    prompt = f'''User goal: {goal}

User's network (these are the ONLY people you may recommend; do NOT invent or guess anyone else):
{network}'''
    return _chat(
        "You are the user's chief of staff. You recommend ONLY from the contacts provided below; "
        "you MUST NOT invent, guess, or pull names from outside the provided list. "
        "If none of the provided contacts are a clear fit, say so honestly and suggest what kind of "
        "person they should look for instead. Output plain text:\n\n"
        "Possible contacts:\n- Name - why they're a fit (based only on info provided)\n\n"
        "Suggested message draft:\n<draft using only real info from the list>\n",
        prompt, max_tokens=800, temperature=0.4,
    )


def weekly_reconnect_suggestions(contacts: list) -> str:
    """Suggest who to reconnect with this week based on last-interaction dates."""
    lines = []
    for c in contacts[:30]:
        if not c.get("last_at"): continue
        last = str(c["last_at"])[:10]
        lines.append(f"- {c.get('name') or c.get('email','')} ({c.get('email','')}) - last contact {last}")
    if not lines:
        return ("No contacts with email history yet.\n\n"
                "Connect your inbox in Mail Hub so I can analyze who you've been talking to, "
                "then I'll suggest who to reconnect with each week.")
    block = "\n".join(lines)
    return _chat(
        "Suggest 3-5 people the user should reconnect with this week from the list provided. "
        "You MUST only reference names and emails that appear in the list. Do NOT invent anyone. "
        "For each, give a one-line reason based only on the data shown and a short suggested opening message. Plain text only.",
        f"Contacts ordered by most recent contact (top = recent, bottom = stale):\n{block}",
        max_tokens=600, temperature=0.4,
    )


def extract_meetings_from_emails(emails: list) -> str:
    """Parse recent emails and list any scheduled meetings with details."""
    if not emails:
        return "No recent emails to analyze."
    lines = []
    for e in emails[:30]:
        dt = str(e.get("received_at",""))[:16]
        subj = (e.get("subject") or "").strip()[:120]
        prev = (e.get("body_preview") or "").strip().replace("\n"," ")[:300]
        lines.append(f"[{dt}] FROM {e.get('from_email','')} SUBJECT: {subj}\n  {prev}")
    hist = "\n\n".join(lines)
    return _chat(
        "You are scanning the user's recent emails for scheduled meetings or calls (confirmed or proposed). "
        "List EACH meeting found in plain text:\n\n"
        "Meeting: <one-line title>\nWhen: <date/time if found, else 'TBD - needs confirmation'>\n"
        "With: <person + email>\nWhat to prep: <2-3 bullet points>\n"
        "Suggested agenda:\n- item 1\n- item 2\n- item 3\n\n"
        "---\n\nIf no meetings found, say 'No scheduled meetings detected in your recent emails.'\n"
        "No markdown, no emojis.",
        hist, max_tokens=1200, temperature=0.3,
    )
