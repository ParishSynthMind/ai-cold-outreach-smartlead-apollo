from types import SimpleNamespace

from outreach.health import check_accounts, check_dns

from .conftest import FakeSmartlead


def test_accounts_ok(settings):
    assert check_accounts(FakeSmartlead(), settings).ok


def test_accounts_problems(settings):
    sl = FakeSmartlead(accounts=[{"id": 101, "from_email": "s@x", "is_smtp_success": False,
                                  "warmup_details": {"status": "INACTIVE"}, "message_per_day": 2}])
    h = check_accounts(sl, settings)
    assert not h.ok and any("warm-up" in w for w in h.warnings) and any("above" in w for w in h.warnings)
    missing = check_accounts(FakeSmartlead(accounts=[]), settings)
    assert any("not in this Smartlead workspace" in e for e in missing.errors)


def resolver(records):
    def resolve(name, rtype):
        if name not in records:
            raise LookupError("NXDOMAIN")
        return [SimpleNamespace(strings=[v.encode()]) for v in records[name]]
    return resolve


def test_dns_all_good():
    r = resolver({"ex.com": ["v=spf1 include:_spf.google.com ~all"],
                  "google._domainkey.ex.com": ["v=DKIM1; k=rsa; p=MIIB"],
                  "_dmarc.ex.com": ["v=DMARC1; p=quarantine; rua=mailto:d@ex.com"]})
    h = check_dns("ex.com", resolver=r)
    assert h.ok and not h.warnings and len(h.info) == 3


def test_dns_problems():
    r = resolver({"ex.com": ["v=spf1 +all", "v=spf1 -all"], "_dmarc.ex.com": ["v=DMARC1; p=none"]})
    h = check_dns("ex.com", resolver=r)
    assert any("exactly one" in e for e in h.errors) and any("DKIM" in e for e in h.errors)
    assert any("p=none" in w for w in h.warnings)
    h2 = check_dns("ex.com", resolver=resolver({"ex.com": ["v=spf1 +all"]}))
    assert any("+all" in e for e in h2.errors) and any("DMARC" in e for e in h2.errors)


def test_dns_no_domain():
    assert check_dns("").warnings


def test_dns_revoked_dkim_key():
    r = resolver({"ex.com": ["v=spf1 -all"], "google._domainkey.ex.com": ["v=DKIM1; p="],
                  "_dmarc.ex.com": ["v=DMARC1; p=reject"]})
    assert any("revoked" in e for e in check_dns("ex.com", resolver=r).errors)
