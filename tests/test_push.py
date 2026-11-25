import pytest

from outreach.approval import ApprovalError, Queue
from outreach.leads import Lead
from outreach.push import push_approved
from outreach.store import Store
from outreach.validate import validate_draft

from .conftest import FakeSmartlead, good_draft


def setup(tmp_path, settings, emails=("jo@bakery.example.com",), approve=True):
    st = Store(str(tmp_path / "t.db"))
    q = Queue(str(tmp_path / "q.json"))
    for e in emails:
        lead = Lead(email=e, first_name="Jo", company="Riverside Bakery", consent_basis="fair",
                    notes="Asked at the Leeds food fair about taking wholesale orders online.")
        d = good_draft()
        q.add(lead, d, validate_draft(d, lead, settings))
        if approve:
            q.approve(e, "tester", settings)
    return st, q


def test_nothing_pushed_without_approval(tmp_path, settings):
    st, q = setup(tmp_path, settings, approve=False)
    sl = FakeSmartlead()
    rep = push_approved(sl, st, q, settings)
    assert rep.pushed == [] and sl.writes() == []


def test_campaign_built_once_with_footer_cap_and_plain_text(tmp_path, settings):
    st, q = setup(tmp_path, settings)
    sl = FakeSmartlead()
    rep = push_approved(sl, st, q, settings)
    camp = sl.campaigns[int(rep.campaign_id)]
    assert rep.created_campaign and rep.pushed == ["jo@bakery.example.com"]
    assert camp["schedule"]["max_new_leads_per_day"] == 5 and camp["accounts"] == [101]
    assert camp["settings"]["send_as_plain_text"] is True
    assert camp["settings"]["track_settings"] == ["DONT_TRACK_EMAIL_OPEN", "DONT_TRACK_LINK_CLICK"]
    assert camp["sequences"][1]["subject"] == "" and camp["sequences"][1]["seq_delay_details"]["delay_in_days"] == 3
    body = next(iter(sl.leads.values()))["custom_fields"]["body_1"]
    assert "12 Example Street" in body and "unsubscribe" in body and body.startswith("<p>Hi Jo,</p>")
    # second run: same campaign, no duplicate lead
    rep2 = push_approved(sl, st, q, settings)
    assert len(sl.campaigns) == 1 and rep2.pushed == [] and len(sl.leads) == 1


def test_suppressed_and_domain_suppressed_never_reach_api(tmp_path, settings):
    st, q = setup(tmp_path, settings, emails=("a@one.example.com", "b@two.example.com"))
    st.suppress("a@one.example.com", "asked", "manual")
    st.suppress("@two.example.com", "company opt-out", "manual")
    sl = FakeSmartlead()
    rep = push_approved(sl, st, q, settings)
    assert sl.writes() == [] and len(rep.skipped) == 2


def test_daily_cap_enforced_locally(tmp_path, settings):
    s = settings.__class__(**{**settings.__dict__, "daily_cap": 2})
    st, q = setup(tmp_path, s, emails=[f"p{i}@x{i}.example.com" for i in range(4)])
    rep = push_approved(FakeSmartlead(), st, q, s)
    assert len(rep.pushed) == 2 and sum("daily cap" in r for _, r in rep.skipped) == 2
    rep2 = push_approved(FakeSmartlead(), st, q, s)
    assert rep2.pushed == []


def test_dry_run_makes_no_calls(tmp_path, settings):
    st, q = setup(tmp_path, settings)
    sl = FakeSmartlead()
    rep = push_approved(sl, st, q, settings, dry_run=True)
    assert rep.pushed and sl.log == [] and st.get("campaign_id") is None


def test_failed_lead_stays_retryable_and_skip_is_recorded(tmp_path, settings):
    st, q = setup(tmp_path, settings, emails=("bad@x.example.com", "dup@y.example.com"))
    sl = FakeSmartlead()
    sl.fail_add, sl.skip_add = {"bad@x.example.com"}, {"dup@y.example.com"}
    rep = push_approved(sl, st, q, settings)
    assert rep.failed[0][0] == "bad@x.example.com" and not st.was_pushed("bad@x.example.com")
    assert q.find("bad@x.example.com")["status"] == "approved"
    assert st.was_pushed("dup@y.example.com")


def test_flagged_draft_needs_accept_warnings(tmp_path, settings):
    st, q = setup(tmp_path, settings, approve=False)
    item = q.find("jo@bakery.example.com")
    item["draft"]["unsure"], item["draft"]["unsure_reason"] = True, "thin"
    with pytest.raises(ApprovalError, match="accept-warnings"):
        q.approve("jo@bakery.example.com", "t", settings)
    q.approve("jo@bakery.example.com", "t", settings, accept_warnings=True)
    item["draft"]["body_1"] += " — edited"
    item["status"] = "pending"
    with pytest.raises(ApprovalError, match="still fails"):
        q.approve("jo@bakery.example.com", "t", settings, accept_warnings=True)
