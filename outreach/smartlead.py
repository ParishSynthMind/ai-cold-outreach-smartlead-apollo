"""Smartlead API client (https://api.smartlead.ai). The key goes in the api_key query parameter.

Smartlead's own documentation is not consistent about a few paths and values (the campaign
status value is START in the older reference and ACTIVE in the newer guides; webhook routes
appear in two forms). Every path lives in ENDPOINTS below so it can be corrected in one place,
and activate() tries START, then ACTIVE. These have NOT been checked against a live account
yet: see the Status section of the README.
"""
from .http import ApiError, HttpClient

BASE_URL = "https://server.smartlead.ai/api/v1"
LEADS_PER_CALL = 100  # Smartlead accepts more per call; small batches keep failures small

ENDPOINTS = {
    "create_campaign": ("POST", "/campaigns/create"),
    "get_campaign": ("GET", "/campaigns/{cid}"),
    "delete_campaign": ("DELETE", "/campaigns/{cid}"),
    "sequences": ("POST", "/campaigns/{cid}/sequences"),
    "schedule": ("POST", "/campaigns/{cid}/schedule"),
    "settings": ("POST", "/campaigns/{cid}/settings"),
    "status": ("POST", "/campaigns/{cid}/status"),
    "list_accounts": ("GET", "/email-accounts/"),
    "attach_accounts": ("POST", "/campaigns/{cid}/email-accounts"),
    "add_leads": ("POST", "/campaigns/{cid}/leads"),
    "list_leads": ("GET", "/campaigns/{cid}/leads"),
    "message_history": ("GET", "/campaigns/{cid}/leads/{lid}/message-history"),
    "unsubscribe_lead": ("POST", "/campaigns/{cid}/leads/{lid}/unsubscribe"),
    "block_list": ("POST", "/leads/add-domain-block-list"),
    "webhook": ("POST", "/campaigns/{cid}/webhooks"),
    "analytics": ("GET", "/campaigns/{cid}/analytics"),
}
START_VALUES = ("START", "ACTIVE")
WEBHOOK_EVENTS = ("EMAIL_REPLY", "LEAD_UNSUBSCRIBED", "EMAIL_BOUNCE")


class SmartleadClient(HttpClient):
    service = "smartlead"

    def __init__(self, api_key, base_url=BASE_URL, **kw):
        super().__init__(api_key, base_url, **kw)

    def _auth(self, headers, params):
        params["api_key"] = self._key
        return headers, params

    def _call(self, name, json=None, params=None, **ids):
        method, path = ENDPOINTS[name]
        return self.request(method, path.format(**{k: str(v) for k, v in ids.items()}), json=json, params=params)

    # campaign
    def create_campaign(self, name):
        data = self._call("create_campaign", json={"name": name})
        cid = data.get("id") or (data.get("campaign") or {}).get("id")
        if not cid:
            raise ApiError(self.service, 200, "create campaign returned no id", "/campaigns/create")
        return cid

    def get_campaign(self, cid):
        return self._call("get_campaign", cid=cid)

    def delete_campaign(self, cid):
        return self._call("delete_campaign", cid=cid)

    def save_sequences(self, cid, sequences):
        return self._call("sequences", json={"sequences": sequences}, cid=cid)

    def update_schedule(self, cid, schedule):
        return self._call("schedule", json=schedule, cid=cid)

    def update_settings(self, cid, settings):
        return self._call("settings", json=settings, cid=cid)

    def set_status(self, cid, status):
        return self._call("status", json={"status": status}, cid=cid)

    def activate(self, cid):
        last = None
        for value in START_VALUES:
            try:
                return self.set_status(cid, value)
            except ApiError as exc:
                if exc.status != 400:
                    raise
                last = exc
        raise last

    def pause(self, cid):
        return self.set_status(cid, "PAUSED")

    def analytics(self, cid):
        return self._call("analytics", cid=cid)

    # sending accounts
    def list_email_accounts(self, limit=100):
        out, offset = [], 0
        while True:
            page = self._call("list_accounts", params={"offset": offset, "limit": limit})
            page = page if isinstance(page, list) else page.get("data") or []
            out.extend(page)
            if len(page) < limit:
                return out
            offset += limit

    def attach_email_accounts(self, cid, account_ids):
        return self._call("attach_accounts", json={"email_account_ids": list(account_ids)}, cid=cid)

    # leads
    def add_leads(self, cid, lead_list):
        """Adds in batches. Smartlead is told NOT to ignore its block list, unsubscribes or duplicates."""
        results = []
        for i in range(0, len(lead_list), LEADS_PER_CALL):
            results.append(self._call("add_leads", cid=cid, json={
                "lead_list": lead_list[i:i + LEADS_PER_CALL],
                "settings": {"ignore_global_block_list": False, "ignore_unsubscribe_list": False,
                             "ignore_duplicate_leads_in_other_campaign": False},
            }))
        return results

    def list_campaign_leads(self, cid, limit=100):
        out, offset = [], 0
        while True:
            page = self._call("list_leads", params={"offset": offset, "limit": limit}, cid=cid)
            rows = page.get("data") or [] if isinstance(page, dict) else page
            out.extend(rows)
            if len(rows) < limit:
                return out
            offset += limit

    def message_history(self, cid, lead_id):
        data = self._call("message_history", cid=cid, lid=lead_id)
        return data.get("history") or [] if isinstance(data, dict) else data

    def unsubscribe_lead(self, cid, lead_id):
        return self._call("unsubscribe_lead", cid=cid, lid=lead_id)

    def add_to_block_list(self, emails_or_domains):
        return self._call("block_list", json={"domain_block_list": list(emails_or_domains)})

    # webhooks
    def add_webhook(self, cid, url, name="reply-handler", events=WEBHOOK_EVENTS):
        return self._call("webhook", cid=cid, json={
            "id": None, "name": name, "webhook_url": url, "event_types": list(events), "categories": []})
