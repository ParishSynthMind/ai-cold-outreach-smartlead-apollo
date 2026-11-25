"""Run the whole CLI flow against in-memory Apollo and Smartlead fakes (no keys, no network
except Claude, which is also faked unless --live-claude is given). Used for the README
transcript and as a smoke test of the wiring.

  python scripts/demo_mocked.py OUT_DIR
"""
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outreach.cli import main  # noqa: E402
from tests.conftest import FakeApollo, FakeProvider, FakeSmartlead, enriched, person  # noqa: E402
from tests.test_cli import draft_for  # noqa: E402


def run(base, label, argv, **clients):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(base + argv, **clients)
    return f"$ python -m outreach {label}\n{buf.getvalue()}" + (f"(exit {code})\n" if code else "")


def demo(out_dir):
    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cfg = json.loads((ROOT / "samples" / "campaign.example.json").read_text())
    (out / "campaign.json").write_text(json.dumps(cfg))
    base = ["--config", str(out / "campaign.json"), "--data-dir", str(out / "data"), "--mask"]
    people = [person(i) for i in range(1, 8)] + [person(8, has_email=False)]
    enrich = {f"ap{i}": enriched(i) for i in range(1, 8)}
    enrich["ap2"] = enriched(2, email="pat2@gmail.com")
    enrich["ap3"] = enriched(3, email_status="guessed")
    ap, sl = FakeApollo(people, enrich, total=214), FakeSmartlead()
    prov = FakeProvider([draft_for(1), draft_for(4), draft_for(5)])
    t = run(base, "prospect", ["prospect"], apollo=ap)
    t += run(base, "draft data/leads.apollo.csv", ["draft", str(out / "data" / "leads.apollo.csv")], provider=prov)
    t += run(base, "push            # nothing approved yet", ["push"], smartlead=sl)
    t += run(base, "approve --all-clean", ["approve", "--all-clean"])
    t += run(base, "push --dry-run", ["push", "--dry-run"], smartlead=sl)
    t += run(base, "push", ["push"], smartlead=sl)
    t += run(base, "activate --yes", ["activate", "--yes"], smartlead=sl)
    lid = next(k for k, v in sl.leads.items() if v["email"].startswith("pat4"))
    sl.history[lid] = [{"type": "REPLY", "email_body": "Thanks but please unsubscribe me.", "message_id": "m1"}]
    t += run(base, "sync", ["sync"], smartlead=sl, provider=FakeProvider([]))
    t += run(base, "suppress list", ["suppress", "list"])
    t += run(base, "stats --offline", ["stats", "--offline"])
    camp = next(iter(sl.campaigns.values()))
    sent = {"schedule": camp["schedule"], "settings": camp["settings"], "accounts": camp["accounts"],
            "block_list": sl.blocked, "unsubscribed_lead_ids": sl.unsubscribed}
    t += "\n# what the fake Smartlead received\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in sent.items()) + "\n"
    (out / "mocked-run.txt").write_text(t.replace(str(out) + "/", ""))
    print(t)


if __name__ == "__main__":
    demo(sys.argv[1] if len(sys.argv) > 1 else "live/mocked")
