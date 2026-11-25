"""Apollo -> leads. Search is free; enrichment (to get the work email) costs credits.

Order, so credits are only spent on people we could actually email:
1. search with the campaign's filters (0 credits, no emails returned)
2. drop people Apollo says have no email, and anyone enriched before
3. enrich at most max_enrich_per_run people (1 credit each when Apollo finds them)
4. drop unverified addresses, personal mailboxes, suppressed addresses and domains,
   countries outside allowed_countries, and duplicates
"""
import logging
from dataclasses import dataclass, field

from .http import ApiError
from .leads import EMAIL_RE, FREE_MAIL, Lead, normalise_email

log = logging.getLogger(__name__)


@dataclass
class ProspectReport:
    found: int = 0
    total_available: int = None
    enriched: int = 0
    credits_used: int = 0
    leads: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (who, reason)
    stopped: str = ""


def lead_from_person(person, lawful_basis):
    org = person.get("organization") or {}
    notes = []
    if org.get("industry"):
        notes.append(f"Industry: {org['industry']}.")
    place = ", ".join(x for x in (person.get("city"), person.get("country")) if x)
    if place:
        notes.append(f"Based in {place}.")
    return Lead(
        email=normalise_email(person.get("email")),
        first_name=(person.get("first_name") or "").strip(),
        last_name=(person.get("last_name") or "").strip(),
        company=(org.get("name") or person.get("organization_name") or "").strip(),
        role=(person.get("title") or "").strip(),
        website=(org.get("website_url") or "").strip(),
        notes=" ".join(notes),
        country=(person.get("country") or "").strip(),
        consent_basis=lawful_basis,
        source="apollo",
        apollo_id=str(person.get("id") or ""),
        email_status=(person.get("email_status") or "").strip(),
    )


def prospect(apollo, store, settings, queue=None, page=1):
    rep = ProspectReport()
    try:
        people, rep.total_available = apollo.search_people(settings.apollo_search, page=page,
                                                           per_page=settings.max_leads_per_search)
    except ApiError as exc:
        rep.stopped = f"search failed: {exc}"
        return rep
    rep.found = len(people)
    allowed = {c.lower() for c in settings.allowed_countries}
    seen = set()
    for person in people:
        pid = str(person.get("id") or "")
        who = f"{person.get('first_name', '?')} at {(person.get('organization') or {}).get('name', '?')}"
        if not pid:
            rep.skipped.append((who, "no Apollo id"))
            continue
        if person.get("has_email") is False:
            rep.skipped.append((who, "Apollo has no email for this person"))
            continue
        if store.enriched_before(pid):
            rep.skipped.append((who, "enriched before"))
            continue
        if rep.enriched >= settings.max_enrich_per_run:
            rep.skipped.append((who, f"enrichment cap of {settings.max_enrich_per_run} per run reached"))
            continue
        try:
            full = apollo.enrich_person(pid)
        except ApiError as exc:
            if exc.is_plan_limit or exc.status in (401, 402, 403):
                rep.stopped = f"enrichment refused: {exc}"
                break
            rep.skipped.append((who, f"enrichment error: {exc.status}"))
            continue
        rep.enriched += 1
        found = bool(full.get("email"))
        store.record_credit(pid, found)
        rep.credits_used += 1 if found else 0
        lead = lead_from_person(dict(full, id=pid), settings.lawful_basis)
        reason = None
        if not EMAIL_RE.match(lead.email):
            reason = "no usable work email"
        elif lead.email_status and lead.email_status != "verified":
            reason = f"email not verified ({lead.email_status})"
        elif lead.domain in FREE_MAIL:
            reason = "personal mailbox, not a business address"
        elif store.is_suppressed(lead.email) or store.is_suppressed("@" + lead.domain):
            reason = "on suppression list"
        elif allowed and lead.country.lower() not in allowed:
            reason = f"country not allowed ({lead.country or 'unknown'})"
        elif not lead.first_name or not lead.company:
            reason = "missing first name or company"
        elif lead.email in seen or (queue is not None and queue.find(lead.email)):
            reason = "duplicate"
        if reason:
            rep.skipped.append((who, reason))
            continue
        seen.add(lead.email)
        rep.leads.append(lead)
    return rep
