"""Campaign settings, loaded from a JSON file (see samples/campaign.example.json)."""
import json
from dataclasses import dataclass, field, fields

DEFAULT_UNSUBSCRIBE = "If you'd rather not hear from me, reply \"unsubscribe\" and I won't email you again."

# Apollo search filters this project passes through (all optional). Anything else is refused,
# so a typo never turns into an unfiltered search.
APOLLO_FILTERS = (
    "person_titles", "include_similar_titles", "person_seniorities", "person_locations",
    "organization_locations", "q_organization_domains_list", "organization_num_employees_ranges",
    "q_keywords", "contact_email_status",
)


@dataclass
class Settings:
    campaign_name: str
    sender_name: str
    sender_company: str
    sender_postal_address: str          # CAN-SPAM: a real postal address in every email
    offer: str
    call_to_action: str
    lawful_basis: str                   # why these people may be emailed (recorded on every lead)
    sending_account_ids: list           # Smartlead email account ids to send from
    apollo_search: dict = field(default_factory=dict)
    max_leads_per_search: int = 25      # Apollo search page size we keep
    max_enrich_per_run: int = 5         # Apollo enrichment costs credits: hard cap per run
    allowed_countries: list = field(default_factory=list)  # empty = any; else lead country must match
    daily_cap: int = 5                  # new leads per day (Smartlead max_new_leads_per_day + local ledger)
    timezone: str = "Europe/London"
    start_hour: str = "09:00"
    end_hour: str = "17:00"
    send_days: list = field(default_factory=lambda: [1, 2, 3, 4, 5])  # 1 = Monday ... 7 = Sunday
    min_minutes_between_emails: int = 10
    follow_up_delay_days: int = 3
    unsubscribe_line: str = DEFAULT_UNSUBSCRIBE
    sending_domain: str = ""            # for the SPF / DKIM / DMARC check
    dkim_selectors: list = field(default_factory=lambda: ["google", "default", "selector1"])
    claude_max_calls: int = 40          # cost guard: model calls per run
    claude_max_usd: float = 0.50        # cost guard: estimated spend per run
    max_cap: int = 50                   # hard ceiling: refuse a daily_cap above this

    def __post_init__(self):
        if not self.sending_account_ids:
            raise ValueError("sending_account_ids must list at least one Smartlead email account id")
        if not 1 <= int(self.daily_cap) <= self.max_cap:
            raise ValueError(f"daily_cap must be between 1 and {self.max_cap}")
        if "unsubscribe" not in self.unsubscribe_line.lower():
            raise ValueError("unsubscribe_line must tell the reader how to unsubscribe")
        if len(self.sender_postal_address.strip()) < 10:
            raise ValueError("sender_postal_address is required (CAN-SPAM) and looks too short")
        if len(self.lawful_basis.strip()) < 10:
            raise ValueError("lawful_basis must say why these people can be emailed")
        unknown = set(self.apollo_search) - set(APOLLO_FILTERS)
        if unknown:
            raise ValueError(f"unknown apollo_search filters: {', '.join(sorted(unknown))}")
        if any(d not in range(1, 8) for d in self.send_days):
            raise ValueError("send_days use 1 (Monday) to 7 (Sunday)")
        if not 0 <= int(self.max_enrich_per_run) <= 100:
            raise ValueError("max_enrich_per_run must be 0 to 100")
        if self.claude_max_calls < 1 or self.claude_max_usd <= 0:
            raise ValueError("claude_max_calls and claude_max_usd must be positive")

    def footer(self):
        """Appended by code to every email, after the model's text: who we are and how to stop."""
        return f"{self.sender_name}, {self.sender_company}, {self.sender_postal_address}\n{self.unsubscribe_line}"

    def sender_text(self):
        """Everything the model is allowed to say about the sender, as one string."""
        return " ".join([self.sender_name, self.sender_company, self.offer, self.call_to_action])


def load_settings(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    known = {f.name for f in fields(Settings)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    return Settings(**raw)
