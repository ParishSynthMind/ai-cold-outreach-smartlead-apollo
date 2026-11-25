import json

from outreach.cli import main

from .conftest import FakeApollo, FakeProvider, FakeSmartlead, enriched, good_draft, person


def config(tmp_path, **over):
    cfg = {
        "campaign_name": "Bakeries Nov", "sender_name": "Sam Carter", "sender_company": "Carter Automation",
        "sender_postal_address": "12 Example Street, Leeds LS1 1AA, United Kingdom",
        "offer": "I set up simple systems that turn website and WhatsApp orders into one daily order sheet",
        "call_to_action": "Would a 15 minute call next week be useful?",
        "lawful_basis": "B2B legitimate interest, LIA recorded 2026-11-25",
        "sending_account_ids": [101], "apollo_search": {"person_titles": ["owner"]},
        "allowed_countries": ["United Kingdom"], "max_enrich_per_run": 2,
    }
    cfg.update(over)
    p = tmp_path / "campaign.json"
    p.write_text(json.dumps(cfg))
    return ["--config", str(p), "--data-dir", str(tmp_path / "data")]


def draft_for(i):
    d = good_draft(facts_used=[{"field": "company", "value": f"Bakery {i}"}])
    d["body_1"] = d["body_1"].replace("Hi Jo", f"Hi Pat{i}").replace(
        "You asked at the Leeds food fair about taking wholesale orders online.",
        f"I noticed Bakery {i} is in food production in Leeds.")
    d["body_2"] = d["body_2"].replace("Hi Jo", f"Hi Pat{i}")
    return d


def test_full_flow(tmp_path, capsys):
    base = config(tmp_path)
    ap = FakeApollo([person(1), person(2), person(3)], {f"ap{i}": enriched(i) for i in (1, 2, 3)})
    sl = FakeSmartlead()
    prov = FakeProvider([draft_for(1), draft_for(2)])

    assert main(base + ["prospect"], apollo=ap) == 0
    out = capsys.readouterr().out
    assert "2 lead(s) ready" in out and "enrichment cap" in out and len(ap.enrich_calls) == 2

    leads_csv = str(tmp_path / "data" / "leads.apollo.csv")
    assert main(base + ["draft", leads_csv], provider=prov) == 0
    out = capsys.readouterr().out
    assert out.count("clean") == 2 and "2 model calls" in out

    # nothing approved yet: push sends nothing
    assert main(base + ["push"], smartlead=sl) == 0
    assert sl.writes() == []

    assert main(base + ["approve", "--all-clean"]) == 0
    assert main(base + ["--mask", "push", "--dry-run"], smartlead=sl) == 0
    assert "DRY RUN" in capsys.readouterr().out and sl.writes() == []

    assert main(base + ["push"], smartlead=sl) == 0
    assert len(sl.leads) == 2 and len(sl.campaigns) == 1

    assert main(base + ["activate"], smartlead=sl) == 1  # needs --yes
    assert main(base + ["activate", "--yes"], smartlead=sl) == 0
    assert next(iter(sl.campaigns.values()))["status"] == "ACTIVE"

    lid = next(k for k, v in sl.leads.items() if v["email"] == "pat1@bakery1.example.com")
    sl.history[lid] = [{"type": "REPLY", "email_body": "Please unsubscribe me", "message_id": "m1"}]
    assert main(base + ["sync"], smartlead=sl, provider=FakeProvider([])) == 0
    assert sl.blocked == ["pat1@bakery1.example.com"] and sl.unsubscribed == [lid]

    assert main(base + ["suppress", "list"]) == 0
    assert "pat1@bakery1.example.com" in capsys.readouterr().out
    assert main(base + ["stats", "--offline"]) == 0
    assert main(base + ["teardown"], smartlead=sl) == 1
    assert main(base + ["teardown", "--yes"], smartlead=sl) == 0 and sl.campaigns == {}


def test_draft_stops_at_cost_guard(tmp_path, capsys):
    base = config(tmp_path, claude_max_calls=1)
    csv = tmp_path / "l.csv"
    csv.write_text("email,first_name,company,consent_basis,notes\n"
                   "jo@a.example.com,Jo,Riverside Bakery,fair,Asked at the Leeds food fair about taking "
                   "wholesale orders online.\nal@b.example.com,Al,B Ltd,fair,\n")
    assert main(base + ["draft", str(csv)], provider=FakeProvider([good_draft(), good_draft()])) == 0
    assert "STOPPED: cost guard" in capsys.readouterr().out


def test_push_refused_when_accounts_fail(tmp_path, capsys):
    base = config(tmp_path)
    assert main(base + ["push"], smartlead=FakeSmartlead(accounts=[])) == 1
    assert "Not pushing" in capsys.readouterr().out


def test_missing_key_is_a_clear_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setattr("outreach.secrets.subprocess.run", lambda *a, **k: type("R", (), {"returncode": 1})())
    assert main(config(tmp_path) + ["prospect"]) == 2
    assert "APOLLO_API_KEY" in capsys.readouterr().err


def test_bad_config_is_a_clear_error(tmp_path, capsys):
    assert main(config(tmp_path, daily_cap=999) + ["review"]) == 2
    assert "daily_cap" in capsys.readouterr().err
