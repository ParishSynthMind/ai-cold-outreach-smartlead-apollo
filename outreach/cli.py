"""Command line: python -m outreach <command>."""
import argparse
import getpass
import logging
import os
import sys

from .approval import ApprovalError, Queue, render_review_page
from .config import load_settings
from .health import check_accounts, check_dns
from .http import ApiError
from .leads import load_leads, save_leads
from .llm import BudgetExceeded, ClaudeProvider, CostGuard
from .push import STATE_CAMPAIGN, link_lead_ids, push_approved, today
from .replies import catch_up, poll, stop_everywhere, write_report
from .secrets import MissingSecret, get_secret
from .store import Store


def mask_email(email):
    user, _, domain = (email or "").partition("@")
    return f"{user[:2]}***@{domain}" if domain else email


class App:
    """Wires settings, store, queue and API clients. Clients are built lazily, so commands
    that do not need them (review, approve, suppress) work offline with no keys."""

    def __init__(self, args, apollo=None, smartlead=None, provider=None):
        self.args = args
        self.settings = load_settings(args.config)
        os.makedirs(args.data_dir, exist_ok=True)
        self.store = Store(os.path.join(args.data_dir, "outreach.db"))
        self.queue = Queue(os.path.join(args.data_dir, "queue.json"))
        self._apollo, self._smartlead, self._provider = apollo, smartlead, provider
        self.guard = CostGuard(self.settings.claude_max_calls, self.settings.claude_max_usd)
        self.show = mask_email if args.mask else (lambda e: e)

    @property
    def apollo(self):
        if self._apollo is None:
            from .apollo import ApolloClient
            self._apollo = ApolloClient(get_secret("APOLLO_API_KEY", "apollo-api-key"))
        return self._apollo

    @property
    def smartlead(self):
        if self._smartlead is None:
            from .smartlead import SmartleadClient
            self._smartlead = SmartleadClient(get_secret("SMARTLEAD_API_KEY", "smartlead-api-key"))
        return self._smartlead

    @property
    def provider(self):
        if self._provider is None:
            self._provider = ClaudeProvider(guard=self.guard)
        elif getattr(self._provider, "guard", "unset") is None:
            self._provider.guard = self.guard
        return self._provider

    def out(self, text=""):
        print(text)

    def path(self, name):
        return os.path.join(self.args.data_dir, name)

    def page(self):
        return render_review_page(self.queue, self.path("review.html"), mask=self.show if self.args.mask else None)


def cmd_prospect(app):
    from .prospect import prospect
    rep = prospect(app.apollo, app.store, app.settings, queue=app.queue, page=app.args.page)
    app.out(f"Apollo search: {rep.found} people on this page"
            + (f" ({rep.total_available} match in total)" if rep.total_available is not None else ""))
    for who, why in rep.skipped:
        app.out(f"  skip {who}: {why}")
    app.out(f"enriched {rep.enriched}, credits used {rep.credits_used} "
            f"(today: {app.store.credits_used(today())})")
    if rep.stopped:
        app.out(f"STOPPED: {rep.stopped}")
    out = app.path("leads.apollo.csv")
    save_leads(rep.leads, out)
    app.out(f"{len(rep.leads)} lead(s) ready in {out}. Next: python -m outreach draft {out}")
    return 1 if rep.stopped and not rep.leads else 0


def cmd_draft(app):
    from .drafting import Drafter
    loaded = load_leads(app.args.leads)
    leads, rejected = loaded.leads, loaded.rejected
    for row, email, reason in rejected:
        app.out(f"rejected row {row} ({app.show(email) or 'blank'}): {reason}")
    drafter = Drafter(app.provider, app.settings)
    made = 0
    for lead in leads[: app.args.limit or None]:
        if app.store.is_suppressed(lead.email) or app.store.is_suppressed("@" + lead.domain):
            app.out(f"skip {app.show(lead.email)}: on suppression list")
            continue
        if app.queue.find(lead.email) or app.store.was_pushed(lead.email):
            app.out(f"skip {app.show(lead.email)}: already drafted or pushed")
            continue
        try:
            draft, check = drafter.draft(lead)
        except BudgetExceeded as exc:
            app.out(f"STOPPED: {exc}")
            break
        app.queue.add(lead, draft, check)
        app.queue.save()  # saved after every lead
        made += 1
        state = "clean" if check.clean else f"FLAGGED ({len(check.errors)} errors, {len(check.warnings)} warnings)"
        app.out(f"drafted {app.show(lead.email)}: {state}")
    app.out(f"{made} new draft(s); {app.guard.summary()}. Review them in {app.page()}")
    return 0


def cmd_review(app):
    for item in app.queue.items:
        app.out(f"{item['status']:9} {app.show(item['email'])}  {(item['draft'] or {}).get('subject_1', '')}")
        for e in item["errors"]:
            app.out(f"          error: {e}")
        for w in item["warnings"]:
            app.out(f"          check: {w}")
    app.out(f"Review page: {app.page()}")
    return 0


def cmd_approve(app):
    reviewer = app.args.reviewer or getpass.getuser()
    if app.args.all_clean:
        done = app.queue.approve_all_clean(reviewer, app.settings)
        app.queue.save()
        app.out(f"approved {len(done)} clean draft(s); flagged ones need approving one by one")
        return 0
    code = 0
    for email in app.args.emails:
        try:
            app.queue.approve(email, reviewer, app.settings, accept_warnings=app.args.accept_warnings)
            app.out(f"approved {app.show(email)}")
        except ApprovalError as exc:
            app.out(f"not approved: {exc}")
            code = 1
    app.queue.save()
    return code


def cmd_reject(app):
    reviewer = app.args.reviewer or getpass.getuser()
    for email in app.args.emails:
        try:
            app.queue.reject(email, reviewer)
            app.out(f"rejected {app.show(email)}")
        except ApprovalError as exc:
            app.out(f"not rejected: {exc}")
    app.queue.save()
    return 0


def print_health(app, h, title):
    app.out(title)
    for line in h.info:
        app.out(f"  OK   {line}")
    for line in h.warnings:
        app.out(f"  WARN {line}")
    for line in h.errors:
        app.out(f"  FAIL {line}")


def cmd_health(app):
    h = check_accounts(app.smartlead, app.settings)
    print_health(app, h, "Smartlead sending accounts")
    d = check_dns(app.settings.sending_domain, app.settings.dkim_selectors)
    print_health(app, d, f"DNS for {app.settings.sending_domain or '(no sending_domain)'}")
    return 0 if h.ok and d.ok else 1


def cmd_dns(app):
    d = check_dns(app.args.domain or app.settings.sending_domain, app.settings.dkim_selectors)
    print_health(app, d, "SPF / DKIM / DMARC")
    return 0 if d.ok else 1


def cmd_push(app):
    if not app.args.dry_run:
        h = check_accounts(app.smartlead, app.settings)
        if not h.ok:
            print_health(app, h, "Not pushing: sending accounts failed checks")
            return 1
    rep = push_approved(app.smartlead, app.store, app.queue, app.settings, dry_run=app.args.dry_run)
    app.queue.save()
    tag = "DRY RUN, nothing sent to Smartlead. " if rep.dry_run else ""
    if not (rep.pushed or rep.skipped or rep.failed):
        app.out("nothing approved to push")
        return 0
    new = " (new)" if rep.created_campaign and not rep.dry_run else ""
    app.out(f"{tag}campaign {rep.campaign_id or '(none)'}{new}")
    for e in rep.pushed:
        app.out(f"  {'would push' if rep.dry_run else 'pushed'} {app.show(e)}")
    for e, why in rep.skipped:
        app.out(f"  skipped {app.show(e)}: {why}")
    for e, err in rep.failed:
        app.out(f"  FAILED {app.show(e)}: {err}")
    if not rep.dry_run and rep.campaign_id and rep.pushed:
        link_lead_ids(app.smartlead, app.store, rep.campaign_id)
    return 1 if rep.failed else 0


def cmd_activate(app):
    cid = app.store.get(STATE_CAMPAIGN)
    if not cid:
        app.out("no campaign yet: run push first")
        return 1
    pending = app.queue.by_status("pending") + app.queue.by_status("flagged")
    if not app.args.yes:
        app.out(f"This starts sending campaign {cid} to the leads already pushed "
                f"({len(app.store.pushed(cid))}). {len(pending)} draft(s) are still unapproved and will NOT be sent."
                " Re-run with --yes to start.")
        return 1
    app.smartlead.activate(cid)
    app.out(f"campaign {cid} started")
    return 0


def cmd_pause(app):
    cid = app.store.get(STATE_CAMPAIGN)
    if cid:
        app.smartlead.pause(cid)
        app.out(f"campaign {cid} paused")
    return 0


def cmd_sync(app):
    cid = app.store.get(STATE_CAMPAIGN)
    if not cid:
        app.out("no campaign yet")
        return 1
    link_lead_ids(app.smartlead, app.store, cid)
    caught = catch_up(app.smartlead, app.store, cid)
    if caught:
        app.out(f"  unsubscribed {len(caught)} earlier opt-out(s) in the campaign")
    new = poll(app.smartlead, app.store, app.provider, cid)
    for r in new:
        app.out(f"  {app.show(r['email'])}: {r.get('label') or r.get('reason') or r['status']}")
    counts = write_report(app.store, app.path("reply-report.md"), mask=app.show if app.args.mask else None)
    app.out(f"{len(new)} new event(s). {counts}. Report: {app.path('reply-report.md')}")
    return 0


def cmd_webhook(app):
    from .webhook import serve
    token = os.environ.get("OUTREACH_WEBHOOK_TOKEN", "")
    if app.args.register:
        cid = app.store.get(STATE_CAMPAIGN)
        if not cid:
            app.out("no campaign yet: run push first")
            return 1
        app.smartlead.add_webhook(cid, app.args.register)
        app.out(f"webhook registered on campaign {cid}")
        return 0
    server = serve(app.args.host, app.args.port, token, app.store, app.provider, app.smartlead)
    app.out(f"listening on http://{app.args.host}:{app.args.port}/smartlead?token=... (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def cmd_suppress(app):
    if app.args.action == "list":
        for s in app.store.suppressed():
            app.out(f"{app.show(s['email'])}  {s['reason']} ({s['source']}, {s['added_at']})")
        return 0
    for email in app.args.emails:
        if app.args.action == "add":
            client = None if app.args.local_only else app.smartlead
            stop_everywhere(app.store, client, email.lower(), app.args.reason, "manual")
            app.out(f"suppressed {app.show(email)}")
        else:
            app.store.unsuppress(email)
            app.out(f"removed {app.show(email)} from the local list (Smartlead's block list is unchanged)")
    return 0


def cmd_stats(app):
    app.out(f"queue: {app.queue.counts()}")
    app.out(f"pushed: {len(app.store.pushed())}, suppressed: {len(app.store.suppressed())}, "
            f"Apollo credits used: {app.store.credits_used()}")
    cid = app.store.get(STATE_CAMPAIGN)
    if cid and not app.args.offline:
        app.out(f"Smartlead analytics: {app.smartlead.analytics(cid)}")
    return 0


def cmd_teardown(app):
    cid = app.store.get(STATE_CAMPAIGN)
    if not cid:
        app.out("nothing to tear down")
        return 0
    if not app.args.yes:
        app.out(f"This pauses and deletes campaign {cid}. Re-run with --yes.")
        return 1
    try:
        app.smartlead.pause(cid)
    except ApiError:
        pass
    app.smartlead.delete_campaign(cid)
    app.store.set(STATE_CAMPAIGN, None)
    app.out(f"campaign {cid} deleted (local suppression list kept)")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="outreach", description=__doc__)
    p.add_argument("--config", default=os.environ.get("OUTREACH_CONFIG", "campaign.json"))
    p.add_argument("--data-dir", default=os.environ.get("OUTREACH_DATA", "data"))
    p.add_argument("--mask", action="store_true", help="mask email addresses in output")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prospect", help="Apollo search + capped enrichment -> data/leads.apollo.csv")
    s.add_argument("--page", type=int, default=1)
    s = sub.add_parser("draft", help="Claude drafts emails into the approval queue")
    s.add_argument("leads")
    s.add_argument("--limit", type=int, default=0)
    sub.add_parser("review")
    s = sub.add_parser("approve")
    s.add_argument("emails", nargs="*")
    s.add_argument("--all-clean", action="store_true")
    s.add_argument("--accept-warnings", action="store_true")
    s.add_argument("--reviewer")
    s = sub.add_parser("reject")
    s.add_argument("emails", nargs="+")
    s.add_argument("--reviewer")
    sub.add_parser("health")
    s = sub.add_parser("dns")
    s.add_argument("domain", nargs="?")
    s = sub.add_parser("push")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("activate")
    s.add_argument("--yes", action="store_true")
    sub.add_parser("pause")
    sub.add_parser("sync")
    s = sub.add_parser("webhook")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--register", metavar="PUBLIC_URL", help="register this URL on the campaign instead")
    s = sub.add_parser("suppress")
    s.add_argument("action", choices=["add", "remove", "list"])
    s.add_argument("emails", nargs="*")
    s.add_argument("--reason", default="asked to stop")
    s.add_argument("--local-only", action="store_true")
    s = sub.add_parser("stats")
    s.add_argument("--offline", action="store_true")
    s = sub.add_parser("teardown")
    s.add_argument("--yes", action="store_true")
    return p


COMMANDS = {
    "prospect": cmd_prospect, "draft": cmd_draft, "review": cmd_review, "approve": cmd_approve,
    "reject": cmd_reject, "health": cmd_health, "dns": cmd_dns, "push": cmd_push, "activate": cmd_activate,
    "pause": cmd_pause, "sync": cmd_sync, "webhook": cmd_webhook, "suppress": cmd_suppress,
    "stats": cmd_stats, "teardown": cmd_teardown,
}


def main(argv=None, **clients):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        app = App(args, **clients)
        return COMMANDS[args.cmd](app)
    except MissingSecret as exc:
        print(f"Missing key: {exc}", file=sys.stderr)
    except ApiError as exc:
        print(f"API error: {exc}" + (" (this looks like a plan limit)" if exc.is_plan_limit else ""), file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    return 2

