"""Replies, unsubscribes and bounces from Smartlead: by webhook, with polling as a fallback.

Whatever the route, the result is the same: replies are sorted by Claude, and anyone who asks
to stop (or bounces) goes on the local suppression list, is unsubscribed in the Smartlead
campaign, and is added to Smartlead's global block list, so no campaign can email them again.
"""
import hashlib
import html
import json
import logging
import re

from .drafting import DraftError, parse_json
from .http import ApiError
from .llm import BudgetExceeded, LLMError

log = logging.getLogger(__name__)

LABELS = ("interested", "not_now", "unsubscribe", "out_of_office", "unclear")
STOP_WORDS = re.compile(r"\b(unsubscribe|remove me|take me off|stop emailing|opt ?out|do not (contact|email))\b", re.I)

CLASSIFY_SYSTEM = """You sort replies to a sales email. Read the reply and pick ONE label:
- interested: wants to talk, asks a question about the offer, asks for details or a call
- not_now: polite no for now, maybe later, bad timing, already has a provider
- unsubscribe: asks to stop, remove, unsubscribe, not interested at all, or is hostile
- out_of_office: an automatic away or vacation reply
- unclear: anything else, or you are not sure
If the reply mixes a stop request with anything else, choose unsubscribe.
Reply with JSON only: {"label": "...", "reason": "<one short sentence>"}"""


def strip_quoted(text):
    out = []
    for line in (text or "").splitlines():
        if re.match(r"^\s*On .+wrote:\s*$", line) or line.strip().startswith("-----Original Message"):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(value):
    value = re.sub(r"(?i)<br\s*/?>|</(div|p)>", "\n", value or "")
    value = re.sub(r"(?is)<(blockquote|style|script).*?</\1>", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value)


def classify(provider, text):
    """A stop request is caught by plain keywords first, so it never depends on the model."""
    if STOP_WORDS.search(text):
        return "unsubscribe", "reply asks to stop (keyword match)"
    try:
        raw = provider.complete(CLASSIFY_SYSTEM, "REPLY:\n" + text[:4000], 500).text
        data = parse_json(raw)
    except BudgetExceeded:
        return "unclear", "cost guard reached, left for a person"
    except (DraftError, LLMError) as exc:
        return "unclear", f"classifier failed: {exc}"
    label = data.get("label")
    if label not in LABELS:
        return "unclear", f"classifier gave unknown label {label!r}"
    return label, str(data.get("reason", ""))[:300]


def stop_everywhere(store, client, email, reason, source):
    """Suppress locally first (works even if Smartlead is down), then tell Smartlead."""
    added = store.suppress(email, reason, source)
    if client is None:
        return added
    cid, lid = store.get("campaign_id"), store.lead_id_for(email)
    try:
        if cid and lid:
            client.unsubscribe_lead(cid, lid)
            store.set(f"unsubscribed:{email}", "1")
        client.add_to_block_list([email])
        store.set(f"pending_block:{email}", None)
    except ApiError as exc:
        log.warning("could not update Smartlead for %s: %s (still suppressed locally)", email, exc)
        store.set(f"pending_block:{email}", reason)
    return added


def catch_up(client, store, campaign_id):
    """Finish stop requests Smartlead hasn't heard about yet: a failed block-list call, or a lead
    whose Smartlead id wasn't known when the request came in. Run on every sync."""
    done = []
    for row in store.pushed(campaign_id):
        email, lid = row["email"], row.get("lead_id")
        if not store.is_suppressed(email):
            continue
        try:
            if lid and not store.get(f"unsubscribed:{email}"):
                client.unsubscribe_lead(campaign_id, lid)
                store.set(f"unsubscribed:{email}", "1")
                done.append(email)
            if store.get(f"pending_block:{email}"):
                client.add_to_block_list([email])
                store.set(f"pending_block:{email}", None)
        except ApiError as exc:
            log.warning("catch-up for %s failed: %s", email, exc)
    return done


def event_key(payload):
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def handle_event(payload, store, provider, client=None):
    """One Smartlead webhook delivery. Safe to call twice with the same payload."""
    etype = (payload.get("event_type") or "").upper()
    key = event_key(payload)
    if store.event_seen(key):
        return {"status": "duplicate"}
    email = (payload.get("to_email") or payload.get("lead_email") or payload.get("email") or "").strip().lower()
    result = {"status": "ignored", "event_type": etype}
    if etype == "EMAIL_REPLY" and email:
        text = strip_quoted(html_to_text(payload.get("reply_body") or "")) or payload.get("preview_text") or ""
        label, reason = classify(provider, text) if text.strip() else ("unclear", "empty reply")
        store.record_reply(key[:24], email, payload.get("subject", ""), payload.get("time_replied"),
                           label, reason, text[:280])
        if label == "unsubscribe":
            stop_everywhere(store, client, email, "replied unsubscribe", "reply")
        result = {"status": "classified", "label": label}
    elif etype in ("LEAD_UNSUBSCRIBED", "EMAIL_UNSUBSCRIBED") and email:
        stop_everywhere(store, client, email, "unsubscribed", "smartlead")
        result = {"status": "suppressed", "reason": "unsubscribed"}
    elif etype == "EMAIL_BOUNCE" and email:
        stop_everywhere(store, client, email, "bounced", "smartlead")
        result = {"status": "suppressed", "reason": "bounced"}
    store.record_event(key, etype)
    return result


def poll(client, store, provider, campaign_id):
    """Fallback when webhooks are not set up: read each pushed lead's thread for new replies."""
    new = []
    for row in store.pushed(campaign_id):
        if not row.get("lead_id") or store.is_suppressed(row["email"]):
            continue
        for msg in client.message_history(campaign_id, row["lead_id"]):
            if (msg.get("type") or "").upper() != "REPLY":
                continue
            payload = {"event_type": "EMAIL_REPLY", "to_email": row["email"], "subject": msg.get("subject", ""),
                       "reply_body": msg.get("email_body", ""), "time_replied": msg.get("time"),
                       "message_id": msg.get("message_id") or msg.get("stats_id")}
            res = handle_event(payload, store, provider, client)
            if res["status"] != "duplicate":
                new.append(dict(res, email=row["email"]))
    return new


def write_report(store, path_md, mask=None):
    show = mask or (lambda e: e)
    rows = store.replies()
    counts = {label: 0 for label in LABELS}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    lines = ["# Reply report", "", " | ".join(f"{k}: {v}" for k, v in counts.items()), "",
             "| Received | From | Label | Why |", "|---|---|---|---|"]
    for r in rows:
        why = (r["reason"] or "").replace("|", "/")
        lines.append(f"| {r['received_at'] or ''} | {show(r['lead_email'] or '')} | {r['label']} | {why} |")
    sup = store.suppressed()
    lines += ["", f"## Suppression list ({len(sup)})", ""]
    lines += [f"- {show(s['email'])}: {s['reason']} ({s['source']})" for s in sup] or ["- empty"]
    with open(path_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return counts
