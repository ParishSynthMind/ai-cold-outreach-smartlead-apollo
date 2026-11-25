"""Apollo.io API client: people search (free, no emails) and enrichment (costs credits).

Official API only (https://docs.apollo.io). No scraping.
- POST /mixed_people/api_search  0 credits, returns people WITHOUT email addresses
- POST /people/match             1 credit per person when it returns data; gives the work email
Personal emails and phone numbers are never requested.
"""
from .http import HttpClient

BASE_URL = "https://api.apollo.io/api/v1"
MAX_PER_PAGE = 100


class ApolloClient(HttpClient):
    service = "apollo"

    def __init__(self, api_key, base_url=BASE_URL, **kw):
        super().__init__(api_key, base_url, **kw)

    def _auth(self, headers, params):
        headers["x-api-key"] = self._key
        headers["Content-Type"] = "application/json"
        headers["Cache-Control"] = "no-cache"
        return headers, params

    def search_people(self, filters, page=1, per_page=25):
        body = dict(filters)
        body.update(page=int(page), per_page=max(1, min(int(per_page), MAX_PER_PAGE)))
        data = self.request("POST", "/mixed_people/api_search", json=body)
        return data.get("people") or [], data.get("total_entries")

    def enrich_person(self, apollo_id):
        """Work email for one person found by search. Never reveals personal emails or phones."""
        body = {"id": apollo_id, "reveal_personal_emails": False, "reveal_phone_number": False}
        data = self.request("POST", "/people/match", json=body)
        return data.get("person") or {}
