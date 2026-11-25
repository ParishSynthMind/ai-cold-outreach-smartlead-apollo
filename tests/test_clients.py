import json

import pytest
import responses

from outreach.apollo import ApolloClient
from outreach.http import ApiError
from outreach.smartlead import SmartleadClient

A = "https://api.apollo.io/api/v1"
S = "https://server.smartlead.ai/api/v1"
KEY = "sk-test-SECRET123"


def nosleep(_):
    pass


@responses.activate
def test_apollo_search_sends_key_header_and_filters():
    responses.post(A + "/mixed_people/api_search", json={"people": [{"id": "1"}], "total_entries": 40})
    c = ApolloClient(KEY, sleep=nosleep)
    people, total = c.search_people({"person_titles": ["owner"]}, per_page=500)
    req = responses.calls[0].request
    assert req.headers["x-api-key"] == KEY and "api_key" not in req.url
    body = json.loads(req.body)
    assert body["person_titles"] == ["owner"] and body["per_page"] == 100
    assert people == [{"id": "1"}] and total == 40


@responses.activate
def test_apollo_enrich_never_reveals_personal_data():
    responses.post(A + "/people/match", json={"person": {"email": "a@b.example.com"}})
    out = ApolloClient(KEY, sleep=nosleep).enrich_person("ap1")
    body = json.loads(responses.calls[0].request.body)
    assert body == {"id": "ap1", "reveal_personal_emails": False, "reveal_phone_number": False}
    assert out["email"] == "a@b.example.com"


@responses.activate
def test_retry_on_429_with_retry_after_then_success():
    waits = []
    responses.post(A + "/people/match", status=429, headers={"Retry-After": "2"})
    responses.post(A + "/people/match", json={"person": {}})
    ApolloClient(KEY, sleep=waits.append).enrich_person("x")
    assert waits == [2.0] and len(responses.calls) == 2


@responses.activate
def test_no_retry_on_400_and_plan_limit_detected():
    responses.post(A + "/mixed_people/api_search", status=403,
                   json={"error": "This endpoint is only available to Apollo users on paid plans"})
    with pytest.raises(ApiError) as e:
        ApolloClient(KEY, sleep=nosleep).search_people({})
    assert e.value.is_plan_limit and len(responses.calls) == 1


@responses.activate
def test_smartlead_key_in_query_but_never_in_errors():
    responses.get(S + "/email-accounts/", status=401, body=f"bad key {KEY} at /x?api_key={KEY}")
    c = SmartleadClient(KEY, sleep=nosleep)
    with pytest.raises(ApiError) as e:
        c.list_email_accounts()
    assert "api_key=" + KEY in responses.calls[0].request.url
    assert KEY not in str(e.value) and KEY not in repr(c)


@responses.activate
def test_smartlead_5xx_retried_then_raises():
    for _ in range(4):
        responses.post(S + "/campaigns/create", status=502)
    with pytest.raises(ApiError):
        SmartleadClient(KEY, sleep=nosleep, max_retries=3).create_campaign("x")
    assert len(responses.calls) == 4


@responses.activate
def test_smartlead_create_campaign_and_activate_fallback():
    responses.post(S + "/campaigns/create", json={"ok": True, "id": 77, "name": "x"})
    responses.post(S + "/campaigns/77/status", status=400, json={"message": "invalid status"})
    responses.post(S + "/campaigns/77/status", json={"ok": True})
    c = SmartleadClient(KEY, sleep=nosleep)
    assert c.create_campaign("x") == 77
    c.activate(77)
    sent = [json.loads(r.request.body)["status"] for r in responses.calls[1:]]
    assert sent == ["START", "ACTIVE"]


@responses.activate
def test_smartlead_add_leads_batches_and_never_ignores_block_list():
    responses.post(S + "/campaigns/5/leads", json={"added_count": 1})
    leads = [{"email": f"a{i}@x.example.com"} for i in range(150)]
    SmartleadClient(KEY, sleep=nosleep).add_leads(5, leads)
    bodies = [json.loads(r.request.body) for r in responses.calls]
    assert [len(b["lead_list"]) for b in bodies] == [100, 50]
    assert all(v is False for v in bodies[0]["settings"].values())


@responses.activate
def test_smartlead_pagination_and_webhook():
    responses.get(S + "/email-accounts/", json=[{"id": i} for i in range(100)])
    responses.get(S + "/email-accounts/", json=[{"id": 100}])
    responses.post(S + "/campaigns/9/webhooks", json={"ok": True})
    c = SmartleadClient(KEY, sleep=nosleep)
    assert len(c.list_email_accounts()) == 101
    c.add_webhook(9, "https://hooks.example.com/smartlead?token=t")
    body = json.loads(responses.calls[-1].request.body)
    assert set(body["event_types"]) == {"EMAIL_REPLY", "LEAD_UNSUBSCRIBED", "EMAIL_BOUNCE"}


def test_empty_key_refused():
    with pytest.raises(ValueError):
        SmartleadClient("")
