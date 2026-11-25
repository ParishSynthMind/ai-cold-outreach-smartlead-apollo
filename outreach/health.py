"""Checks before push and activate: sending accounts, warm-up, caps, and the sending domain's DNS."""
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Health:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    info: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def check_accounts(client, settings):
    h = Health()
    accounts = {str(a.get("id")): a for a in client.list_email_accounts()}
    for aid in settings.sending_account_ids:
        acc = accounts.get(str(aid))
        if not acc:
            h.errors.append(f"sending account {aid} is not in this Smartlead workspace")
            continue
        name = acc.get("from_email") or aid
        if acc.get("is_smtp_success") is False or acc.get("is_imap_success") is False:
            h.errors.append(f"{name}: SMTP or IMAP connection is failing in Smartlead")
        warm = acc.get("warmup_details") or {}
        if not (warm.get("status") == "ACTIVE" or acc.get("warmup_enabled")):
            h.warnings.append(f"{name}: warm-up is off; new mailboxes should warm up 2 to 4 weeks first")
        per_day = acc.get("message_per_day")
        if per_day and settings.daily_cap > int(per_day):
            h.warnings.append(f"{name}: daily_cap {settings.daily_cap} is above the mailbox limit {per_day}")
        h.info.append(f"{name}: ok")
    return h


def _txt(resolver, name):
    try:
        answers = resolver(name, "TXT")
    except Exception:  # NXDOMAIN, no answer, timeout: all mean "not found" here
        return []
    out = []
    for r in answers:
        strings = getattr(r, "strings", None)
        out.append(b"".join(strings).decode() if strings else str(r).strip('"'))
    return out


def check_dns(domain, selectors=("google", "default", "selector1"), resolver=None):
    """SPF, DKIM and DMARC for the sending domain. Needs dnspython (in requirements)."""
    h = Health()
    if not domain:
        h.warnings.append("no sending_domain set, DNS not checked")
        return h
    if resolver is None:
        import dns.resolver
        resolver = dns.resolver.resolve
    spf = [t for t in _txt(resolver, domain) if t.lower().startswith("v=spf1")]
    if not spf:
        h.errors.append(f"SPF: no v=spf1 TXT record on {domain}")
    elif len(spf) > 1:
        h.errors.append(f"SPF: {len(spf)} SPF records on {domain}; there must be exactly one")
    else:
        h.info.append(f"SPF: {spf[0]}")
        if spf[0].rstrip().endswith("+all"):
            h.errors.append("SPF: ends in +all, which lets anyone send as you")
    dkim, revoked = [], []
    for sel in selectors:
        for t in _txt(resolver, f"{sel}._domainkey.{domain}"):
            key = re.search(r"(?:^|;)\s*p=([^;]*)", t)
            if key and key.group(1).strip():
                dkim.append(sel)
            elif key:
                revoked.append(sel)  # "p=" with no key means the key is revoked
    if dkim:
        h.info.append(f"DKIM: key found for selector(s) {', '.join(dict.fromkeys(dkim))}")
    elif revoked:
        h.errors.append(f"DKIM: selector(s) {', '.join(revoked)} publish an empty (revoked) key")
    else:
        h.errors.append(f"DKIM: no key found for selectors {', '.join(selectors)} (add your provider's selector)")
    dmarc = [t for t in _txt(resolver, f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    if not dmarc:
        h.errors.append(f"DMARC: no v=DMARC1 record at _dmarc.{domain}")
    else:
        h.info.append(f"DMARC: {dmarc[0]}")
        if "p=none" in dmarc[0].replace(" ", "").lower():
            h.warnings.append("DMARC: policy is p=none (fine to start; move to quarantine once reports are clean)")
    return h
