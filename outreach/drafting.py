"""Claude writes one first email and one follow-up per lead, as strict JSON.

The footer (sender, postal address, unsubscribe line) is NOT written by the model;
push.py adds it to every email.
"""
import json
import logging
import re

from .llm import BudgetExceeded, LLMError
from .validate import Check, validate_draft

log = logging.getLogger(__name__)

SYSTEM = """You write short, plain cold emails for a small business owner. You only use facts you are given.

Rules:
- Use ONLY facts from the LEAD and SENDER blocks. Never invent numbers, results, clients, names, mutual contacts, compliments, or anything about the lead's business that is not written there.
- If the lead's details are thin or unclear, write a simple honest email and set "unsure" to true with a reason. Do not fill gaps with guesses.
- First email: 50 to 110 words, greeting with the first name, one specific line drawn from the lead's role, company or notes, what the sender offers in one sentence, the call to action, sign off with the sender's first name.
- Follow-up: 20 to 60 words, a polite short nudge in the same thread, no new claims.
- Subject: under 50 characters, lower key, no clickbait, no emojis.
- Plain English, friendly, not salesy. No em dashes or en dashes. No links. No placeholders.
- Do NOT add an unsubscribe line, address or company footer: it is added automatically.
- In "facts_used", list every lead fact you relied on as {"field": <lead field name>, "value": <exact text copied from that field>}.

Reply with JSON only, no prose, in exactly this shape:
{"subject_1": "...", "body_1": "...", "body_2": "...", "facts_used": [{"field": "...", "value": "..."}], "unsure": false, "unsure_reason": ""}"""


class DraftError(Exception):
    pass


def build_prompt(lead, settings):
    sender = {
        "name": settings.sender_name,
        "company": settings.sender_company,
        "offer": settings.offer,
        "call_to_action": settings.call_to_action,
    }
    return (
        "LEAD:\n" + json.dumps(lead.facts(), indent=1, ensure_ascii=False)
        + "\n\nSENDER:\n" + json.dumps(sender, indent=1, ensure_ascii=False)
    )


def parse_json(text):
    text = (text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{") and "{" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    try:
        data = json.loads(text, strict=False)
    except ValueError as exc:
        raise DraftError(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftError("model reply was not a JSON object")
    return data


class Drafter:
    def __init__(self, provider, settings, max_tokens=4000):
        self.provider = provider  # anything with complete(system, user, max_tokens) -> Completion
        self.settings = settings
        self.max_tokens = max_tokens

    def _ask(self, lead, extra=""):
        return self.provider.complete(SYSTEM, build_prompt(lead, self.settings) + extra, self.max_tokens).text

    def draft(self, lead):
        """Returns (draft dict or None, Check). One retry, telling the model what failed.

        BudgetExceeded is raised to the caller so the whole run stops, not just this lead."""
        try:
            draft = parse_json(self._ask(lead))
        except BudgetExceeded:
            raise
        except (DraftError, LLMError) as exc:
            log.warning("draft for %s: %s, retrying", mask(lead.email), exc)
            draft = None
        check = validate_draft(draft, lead, self.settings) if draft else None
        if draft is None or not check.ok:
            problems = check.errors if check else ["your reply was not valid JSON"]
            extra = "\n\nYour previous answer failed these checks, fix them:\n- " + "\n- ".join(problems)
            try:
                draft = parse_json(self._ask(lead, extra))
            except BudgetExceeded:
                raise
            except (DraftError, LLMError) as exc:
                return None, Check(errors=[str(exc)])
            check = validate_draft(draft, lead, self.settings)
        return draft, check


def mask(email):
    user, _, domain = (email or "").partition("@")
    return (user[:1] + "***@" + domain) if domain else "***"
