from outreach.http import ApiError
from outreach.prospect import prospect
from outreach.store import Store

from .conftest import FakeApollo, enriched, person


def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_happy_path_maps_fields_and_logs_credits(tmp_path, settings):
    st = store(tmp_path)
    ap = FakeApollo([person(1)], {"ap1": enriched(1)}, total=12)
    rep = prospect(ap, st, settings)
    lead = rep.leads[0]
    assert lead.email == "pat1@bakery1.example.com"
    assert (lead.company, lead.role, lead.source) == ("Bakery 1", "Owner", "apollo")
    assert lead.consent_basis == settings.lawful_basis and "food production" in lead.notes
    assert rep.credits_used == 1 and st.credits_used() == 1 and rep.total_available == 12
    assert ap.search_calls[0][0] == settings.apollo_search


def test_filters_before_and_after_enrichment(tmp_path, settings):
    st = store(tmp_path)
    st.suppress("pat4@bakery4.example.com", "asked", "manual")
    st.suppress("@bakery7.example.com", "domain opt-out", "manual")
    people = [person(i) for i in range(2, 9)] + [person(9, has_email=False)]
    enrich = {
        "ap2": enriched(2, email_status="guessed"),
        "ap3": enriched(3, email="pat3@gmail.com"),
        "ap4": enriched(4),
        "ap5": enriched(5, country="Germany"),
        "ap6": enriched(6, email=None),
        "ap7": enriched(7),
        "ap8": enriched(8),
    }
    wide = settings.__class__(**{**settings.__dict__, "max_enrich_per_run": 20})
    rep = prospect(FakeApollo(people, enrich), st, wide)
    reasons = [r for _, r in rep.skipped]
    assert [lead.email for lead in rep.leads] == ["pat8@bakery8.example.com"]
    for want in ("not verified", "personal mailbox", "suppression", "country not allowed", "no usable",
                 "Apollo has no email"):
        assert any(want in r for r in reasons), want
    assert sum("suppression" in r for r in reasons) == 2


def test_enrichment_cap_and_never_twice(tmp_path, settings):
    st = store(tmp_path)
    people = [person(i) for i in range(1, 6)]
    ap = FakeApollo(people, {f"ap{i}": enriched(i) for i in range(1, 6)})
    first = prospect(ap, st, settings)
    assert len(ap.enrich_calls) == 3 and len(first.leads) == 3
    second = prospect(ap, st, settings)
    assert ap.enrich_calls[3:] == ["ap4", "ap5"] and len(second.leads) == 2


def test_plan_limit_stops_run(tmp_path, settings):
    ap = FakeApollo([person(1), person(2)], {"ap1": ApiError("apollo", 403, "upgrade your plan", "/people/match")})
    rep = prospect(ap, store(tmp_path), settings)
    assert rep.stopped and ap.enrich_calls == ["ap1"] and not rep.leads


def test_search_failure_reported(tmp_path, settings):
    class Broken(FakeApollo):
        def search_people(self, *a, **k):
            raise ApiError("apollo", 401, "bad key", "/mixed_people/api_search")
    rep = prospect(Broken([]), store(tmp_path), settings)
    assert "search failed" in rep.stopped
