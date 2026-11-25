import json

import pytest

from outreach.config import Settings
from outreach.http import ApiError
from outreach.leads import Lead
from outreach.llm import Completion


def make_settings(**over):
    base = dict(
        campaign_name="Test campaign", sender_name="Sam Carter", sender_company="Carter Automation",
        sender_postal_address="12 Example Street, Leeds LS1 1AA, United Kingdom",
        offer="I set up simple systems that turn website and WhatsApp orders into one daily order sheet",
        call_to_action="Would a 15 minute call next week be useful?",
        lawful_basis="B2B legitimate interest: bakery owners, role-relevant offer, LIA recorded 2026-11-25",
        sending_account_ids=[101], daily_cap=5, sending_domain="outreach.example.com",
        apollo_search={"person_titles": ["owner"], "q_keywords": "bakery"},
        allowed_countries=["United Kingdom"], max_enrich_per_run=3,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def lead():
    return Lead(email="jo@bakery.example.com", first_name="Jo", company="Riverside Bakery",
                consent_basis="asked at food fair", role="Owner", country="United Kingdom",
                notes="Asked at the Leeds food fair about taking wholesale orders online.")


def good_draft(**over):
    d = {
        "subject_1": "Wholesale orders after the food fair",
        "body_1": ("Hi Jo,\n\nYou asked at the Leeds food fair about taking wholesale orders online. "
                   "I set up simple systems that turn website and WhatsApp orders into one daily order sheet, "
                   "so nobody retypes orders by hand.\n\nWould a 15 minute call next week be useful?\n\nSam"),
        "body_2": "Hi Jo,\n\nJust a quick nudge on my note about wholesale orders. Happy to keep it short.\n\nSam",
        "facts_used": [{"field": "notes", "value": "Leeds food fair"}],
        "unsure": False, "unsure_reason": "",
    }
    d.update(over)
    return d


class FakeProvider:
    """Stands in for ClaudeProvider: returns queued replies in order."""

    def __init__(self, replies, guard=None):
        self.replies = list(replies)
        self.calls = []
        self.guard = guard

    def complete(self, system, user, max_tokens=4000):
        if self.guard:
            self.guard.check()
        self.calls.append({"system": system, "user": user})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        out = Completion(text=text, input_tokens=800, output_tokens=300)
        if self.guard:
            self.guard.record(out)
        return out


class FakeSmartlead:
    """In-memory stand-in for SmartleadClient."""

    def __init__(self, accounts=None):
        self.accounts = accounts if accounts is not None else [
            {"id": 101, "from_email": "sam@outreach.example.com", "is_smtp_success": True,
             "is_imap_success": True, "warmup_details": {"status": "ACTIVE"}, "message_per_day": 20}]
        self.campaigns, self.leads, self.history = {}, {}, {}
        self.blocked, self.unsubscribed, self.webhooks = [], [], []
        self.log = []
        self.fail_add, self.skip_add, self.fail_block = set(), set(), False

    def _rec(self, *c):
        self.log.append(c)

    def list_email_accounts(self):
        self._rec("GET", "email-accounts")
        return self.accounts

    def create_campaign(self, name):
        self._rec("POST", "campaigns/create")
        cid = 900 + len(self.campaigns)
        self.campaigns[cid] = {"name": name, "status": "DRAFTED"}
        return cid

    def save_sequences(self, cid, seqs):
        self._rec("POST", "sequences")
        self.campaigns[int(cid)]["sequences"] = seqs

    def update_schedule(self, cid, sched):
        self._rec("POST", "schedule")
        self.campaigns[int(cid)]["schedule"] = sched

    def update_settings(self, cid, s):
        self._rec("POST", "settings")
        self.campaigns[int(cid)]["settings"] = s

    def attach_email_accounts(self, cid, ids):
        self._rec("POST", "email-accounts")
        self.campaigns[int(cid)]["accounts"] = list(ids)

    def add_leads(self, cid, lead_list):
        self._rec("POST", "leads")
        out = []
        for lead in lead_list:
            if lead["email"] in self.fail_add:
                raise ApiError("smartlead", 400, "bad lead", "/leads")
            if lead["email"] in self.skip_add:
                out.append({"added_count": 0, "skipped_count": 1})
                continue
            lid = 5000 + len(self.leads)
            self.leads[lid] = dict(lead, id=lid, campaign=int(cid))
            out.append({"added_count": 1, "skipped_count": 0})
        return out

    def list_campaign_leads(self, cid):
        self._rec("GET", "leads")
        return [{"lead": {"id": k, "email": v["email"]}} for k, v in self.leads.items() if v["campaign"] == int(cid)]

    def message_history(self, cid, lid):
        self._rec("GET", "history")
        return self.history.get(int(lid), [])

    def unsubscribe_lead(self, cid, lid):
        self._rec("POST", "unsubscribe")
        self.unsubscribed.append(int(lid))

    def add_to_block_list(self, items):
        self._rec("POST", "block-list")
        if self.fail_block:
            raise ApiError("smartlead", 503, "down", "/leads/add-domain-block-list")
        self.blocked.extend(items)

    def activate(self, cid):
        self._rec("POST", "status")
        self.campaigns[int(cid)]["status"] = "ACTIVE"

    def pause(self, cid):
        self._rec("POST", "status")
        self.campaigns[int(cid)]["status"] = "PAUSED"

    def delete_campaign(self, cid):
        self._rec("DELETE", "campaign")
        del self.campaigns[int(cid)]

    def add_webhook(self, cid, url, **kw):
        self._rec("POST", "webhooks")
        self.webhooks.append((int(cid), url))

    def analytics(self, cid):
        return {"sent_count": len(self.leads)}

    def writes(self):
        return [c for c in self.log if c[0] != "GET"]


def person(i, **over):
    p = {"id": f"ap{i}", "first_name": f"Pat{i}", "title": "Owner", "has_email": True,
         "organization": {"name": f"Bakery {i}"}}
    p.update(over)
    return p


def enriched(i, **over):
    p = {"id": f"ap{i}", "first_name": f"Pat{i}", "last_name": "Lee", "title": "Owner",
         "email": f"pat{i}@bakery{i}.example.com", "email_status": "verified", "city": "Leeds",
         "country": "United Kingdom",
         "organization": {"name": f"Bakery {i}", "industry": "food production",
                          "website_url": f"http://bakery{i}.example.com"}}
    p.update(over)
    return p


class FakeApollo:
    def __init__(self, people, enrich=None, total=None):
        self.people, self.enrich, self.total = people, enrich or {}, total
        self.search_calls, self.enrich_calls = [], []

    def search_people(self, filters, page=1, per_page=25):
        self.search_calls.append((filters, page, per_page))
        return self.people[:per_page], self.total

    def enrich_person(self, pid):
        self.enrich_calls.append(pid)
        result = self.enrich.get(pid, {})
        if isinstance(result, Exception):
            raise result
        return result
