import pytest

from outreach.leads import load_leads, save_leads

from .conftest import make_settings


def test_settings_footer_has_address_and_unsubscribe(settings):
    f = settings.footer()
    assert "12 Example Street" in f and "unsubscribe" in f.lower()


@pytest.mark.parametrize("over,msg", [
    ({"sending_account_ids": []}, "sending_account_ids"),
    ({"daily_cap": 0}, "daily_cap"),
    ({"daily_cap": 51}, "daily_cap"),
    ({"sender_postal_address": ""}, "postal"),
    ({"lawful_basis": "ok"}, "lawful_basis"),
    ({"unsubscribe_line": "Bye"}, "unsubscribe"),
    ({"apollo_search": {"person_title": ["typo"]}}, "unknown apollo_search"),
    ({"send_days": [0, 1]}, "send_days"),
    ({"max_enrich_per_run": 500}, "max_enrich"),
    ({"claude_max_usd": 0}, "claude_max"),
])
def test_settings_refuses_bad_values(over, msg):
    with pytest.raises(ValueError, match=msg):
        make_settings(**over)


def test_csv_loader_rejects_bad_rows(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("email,first_name,company,consent_basis\n"
                 "a@x.example.com,Ann,X Ltd,opted in\n"
                 "bad,Bo,Y,opted in\n"
                 "A@x.example.com,Ann,X Ltd,opted in\n"
                 "c@z.example.com,Cy,Z,\n")
    r = load_leads(str(p))
    assert [lead.email for lead in r.leads] == ["a@x.example.com"]
    assert [x[2] for x in r.rejected] == ["invalid email", "duplicate", "no consent_basis"]


def test_csv_missing_columns(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("email,first_name\n")
    with pytest.raises(ValueError, match="company"):
        load_leads(str(p))


def test_save_and_reload_keeps_apollo_fields(tmp_path, lead):
    lead.source, lead.apollo_id = "apollo", "ap1"
    path = save_leads([lead], str(tmp_path / "out.csv"))
    back = load_leads(path).leads[0]
    assert back.source == "apollo" and back.apollo_id == "ap1"
    assert "apollo_id" not in back.facts() and "email" not in back.facts()
