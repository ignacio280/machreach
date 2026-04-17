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
