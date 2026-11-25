"""Build the Smartlead campaign and push approved leads to it.

Checks for each lead, in order: approved in the queue -> not on the local suppression list
(address or whole domain) -> never pushed before (local ledger) -> today's local daily cap.
Smartlead is also told not to ignore its own block list, unsubscribes or duplicates.
"""
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .http import ApiError

log = logging.getLogger(__name__)

STATE_CAMPAIGN = "campaign_id"


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def to_html(text):
    """Paragraphs and line breaks as simple HTML (Smartlead stores bodies as HTML)."""
    paras = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    return "".join("<p>" + html.escape(p, quote=False).replace("\n", "<br>") + "</p>" for p in paras)


def with_footer(body, settings):
    return body.rstrip() + "\n\n" + settings.footer()


def sequences(settings):
    """Step 1 and a follow-up in the same thread (empty subject = reply in thread)."""
    return [
        {"seq_number": 1, "seq_delay_details": {"delay_in_days": 0},
         "subject": "{{subject_1}}", "email_body": "{{body_1}}"},
        {"seq_number": 2, "seq_delay_details": {"delay_in_days": settings.follow_up_delay_days},
         "subject": "", "email_body": "{{body_2}}"},
    ]


def schedule(settings):
    return {
        "timezone": settings.timezone,
        "days_of_the_week": list(settings.send_days),
        "start_hour": settings.start_hour,
        "end_hour": settings.end_hour,
        "min_time_btw_emails": settings.min_minutes_between_emails,
        "max_new_leads_per_day": settings.daily_cap,
    }


def campaign_settings(settings):
    return {
        "track_settings": ["DONT_TRACK_EMAIL_OPEN", "DONT_TRACK_LINK_CLICK"],
        "stop_lead_settings": "REPLY_TO_AN_EMAIL",
        "unsubscribe_text": settings.unsubscribe_line,
        "send_as_plain_text": True,
        "follow_up_percentage": 100,
    }


def lead_payload(item, settings):
    lead, draft = item["lead"], item["draft"]
    return {
        "email": lead["email"],
        "first_name": lead["first_name"],
        "last_name": lead.get("last_name", ""),
        "company_name": lead["company"],
        "website": lead.get("website", ""),
        "custom_fields": {
            "subject_1": draft["subject_1"].strip(),
            "body_1": to_html(with_footer(draft["body_1"], settings)),
            "body_2": to_html(with_footer(draft["body_2"], settings)),
        },
    }


@dataclass
class PushReport:
    campaign_id: str = None
    created_campaign: bool = False
    pushed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (email, reason)
    failed: list = field(default_factory=list)  # (email, error)
    dry_run: bool = False


def ensure_campaign(client, store, settings, dry_run=False):
    cid = store.get(STATE_CAMPAIGN)
    if cid:
        return cid, False
    if dry_run:
        return "(would be created)", True
    cid = str(client.create_campaign(settings.campaign_name))
    store.set(STATE_CAMPAIGN, cid)  # saved first, so a failure below never makes a second campaign
    client.save_sequences(cid, sequences(settings))
    client.update_schedule(cid, schedule(settings))
    client.update_settings(cid, campaign_settings(settings))
    client.attach_email_accounts(cid, settings.sending_account_ids)
    log.info("created campaign %s", cid)
    return cid, True


def push_approved(client, store, queue, settings, dry_run=False):
    report = PushReport(dry_run=dry_run)
    room = settings.daily_cap - store.pushed_on(today())
    ready = []
    for item in queue.by_status("approved"):
        email = item["email"]
        domain = "@" + email.rpartition("@")[2]
        if store.is_suppressed(email) or store.is_suppressed(domain):
            report.skipped.append((email, "on suppression list"))
        elif store.was_pushed(email):
            report.skipped.append((email, "already pushed"))
            item["status"] = "pushed"
        elif len(ready) >= room:
            report.skipped.append((email, f"daily cap of {settings.daily_cap} reached, try tomorrow"))
        else:
            ready.append(item)
    if not ready:
        report.campaign_id = store.get(STATE_CAMPAIGN)
        return report

    report.campaign_id, report.created_campaign = ensure_campaign(client, store, settings, dry_run)
    if dry_run:
        report.pushed = [i["email"] for i in ready]
        return report
    for item in ready:  # one lead per call: a bad lead never blocks the others
        email = item["email"]
        try:
            resp = client.add_leads(report.campaign_id, [lead_payload(item, settings)])
        except ApiError as exc:
            report.failed.append((email, str(exc)))
            continue
        first = resp[0] if resp else {}
        if first.get("added_count", 1) == 0:
            report.skipped.append((email, "Smartlead skipped it (block list, unsubscribed or duplicate)"))
        else:
            report.pushed.append(email)
        store.record_push(email, report.campaign_id, None)
        item["status"] = "pushed"
    return report


def link_lead_ids(client, store, campaign_id):
    """Smartlead returns lead ids only when listing; store them so unsubscribes can target the lead."""
    n = 0
    for row in client.list_campaign_leads(campaign_id):
        lead = row.get("lead") or row
        email, lid = (lead.get("email") or "").lower(), lead.get("id")
        if email and lid and store.was_pushed(email) and not store.lead_id_for(email):
            with store.db:
                store.db.execute("UPDATE pushed SET lead_id=? WHERE email=?", (str(lid), email))
            n += 1
    return n
