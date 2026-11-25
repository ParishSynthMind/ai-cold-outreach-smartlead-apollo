"""Leads: from a CSV you already hold, or from Apollo (see prospect.py).

CSV columns. Required: email, first_name, company, consent_basis.
Optional: last_name, role, website, notes, country.

consent_basis is required on purpose: every lead has to say why this person can be
emailed. Apollo leads get it from the campaign's lawful_basis setting.
"""
import csv
import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
REQUIRED = ("email", "first_name", "company", "consent_basis")
OPTIONAL = ("last_name", "role", "website", "notes", "country")
META = ("source", "apollo_id", "email_status")
FIELDS = REQUIRED + OPTIONAL + META
FACT_FIELDS = ("first_name", "last_name", "company", "role", "website", "notes", "country")

# Personal mailbox domains: cold B2B email to these is a consent question, not a legitimate-interest one.
FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com", "hotmail.co.uk",
    "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "mail.com", "yandex.com",
}


@dataclass
class Lead:
    email: str
    first_name: str
    company: str
    consent_basis: str
    last_name: str = ""
    role: str = ""
    website: str = ""
    notes: str = ""
    country: str = ""
    source: str = "csv"
    apollo_id: str = ""
    email_status: str = ""

    def as_dict(self):
        return {f: getattr(self, f) for f in FIELDS}

    def facts(self):
        """The fields the model may use: never the address, consent note or ids."""
        return {k: getattr(self, k) for k in FACT_FIELDS if getattr(self, k)}

    @property
    def domain(self):
        return self.email.rpartition("@")[2]


@dataclass
class LoadResult:
    leads: list = field(default_factory=list)
    rejected: list = field(default_factory=list)  # (row number or id, email, reason)


def normalise_email(value):
    return (value or "").strip().lower()


def check_row(email, first_name, company, consent_basis, seen):
    if not EMAIL_RE.match(email):
        return "invalid email"
    if email in seen:
        return "duplicate"
    if not consent_basis:
        return "no consent_basis"
    if not first_name or not company:
        return "missing first_name or company"
    return None


def load_leads(path):
    result = LoadResult()
    seen = set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for n, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            email = normalise_email(row.get("email"))
            reason = check_row(email, row.get("first_name"), row.get("company"), row.get("consent_basis"), seen)
            if reason:
                result.rejected.append((n, email, reason))
                continue
            seen.add(email)
            data = {f: row.get(f, "") for f in FIELDS}
            data["email"] = email
            data["source"] = data["source"] or "csv"
            result.leads.append(Lead(**data))
    return result


def save_leads(leads, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS))
        w.writeheader()
        for lead in leads:
            w.writerow(lead.as_dict())
    return path
