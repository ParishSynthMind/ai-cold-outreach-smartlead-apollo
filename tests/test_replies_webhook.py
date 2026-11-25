import json
import threading
import urllib.error
import urllib.request

import pytest

from outreach.llm import BudgetExceeded
from outreach.replies import catch_up, classify, handle_event, poll, stop_everywhere, strip_quoted, write_report
from outreach.store import Store
from outreach.webhook import serve

from .conftest import FakeProvider, FakeSmartlead


@pytest.fixture
def st(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.set("campaign_id", "900")
    s.record_push("jo@x.example.com", "900", "5000")
    return s


def reply(body, email="jo@x.example.com", **kw):
    return dict({"event_type": "EMAIL_REPLY", "to_email": email, "reply_body": body, "subject": "Re: hi",
                 "time_replied": "2026-11-25T10:00:00Z", "campaign_id": 900}, **kw)


def test_keyword_unsubscribe_skips_model_and_stops_everywhere(st):
    p, sl = FakeProvider([]), FakeSmartlead()
    r = handle_event(reply("<p>Please remove me from your list.</p>"), st, p, sl)
    assert r == {"status": "classified", "label": "unsubscribe"} and p.calls == []
    assert st.is_suppressed("jo@x.example.com") and sl.unsubscribed == [5000] and sl.blocked == ["jo@x.example.com"]


def test_model_labels_reply_and_duplicate_delivery_ignored(st):
    p = FakeProvider([{"label": "interested", "reason": "asks for a call"}])
    payload = reply("Sounds good, can we talk Tuesday?<br>On Mon, Sam wrote:<br>&gt; old")
    assert handle_event(payload, st, p, FakeSmartlead())["label"] == "interested"
    assert handle_event(payload, st, p, FakeSmartlead()) == {"status": "duplicate"}
    assert len(p.calls) == 1 and "old" not in p.calls[0]["user"]


def test_bad_classifier_output_is_unclear():
    assert classify(FakeProvider(["nonsense"]), "hello")[0] == "unclear"
    assert classify(FakeProvider([{"label": "buy"}]), "hello")[0] == "unclear"
    assert classify(FakeProvider([BudgetExceeded("cap")]), "hello")[0] == "unclear"


@pytest.mark.parametrize("etype,reason", [("LEAD_UNSUBSCRIBED", "unsubscribed"), ("EMAIL_BOUNCE", "bounced")])
def test_unsubscribe_and_bounce_events(st, etype, reason):
    r = handle_event({"event_type": etype, "to_email": "jo@x.example.com"}, st, FakeProvider([]), FakeSmartlead())
    assert r["reason"] == reason and st.is_suppressed("jo@x.example.com")


def test_smartlead_down_still_suppresses_locally(st):
    sl = FakeSmartlead()
    sl.fail_block = True
    stop_everywhere(st, sl, "jo@x.example.com", "asked", "manual")
    assert st.is_suppressed("jo@x.example.com") and st.get("pending_block:jo@x.example.com") == "asked"


def test_unknown_event_ignored(st):
    assert handle_event({"event_type": "EMAIL_OPEN"}, st, FakeProvider([]))["status"] == "ignored"


def test_poll_reads_history_once(st, tmp_path):
    sl = FakeSmartlead()
    sl.history[5000] = [{"type": "SENT", "email_body": "hi"},
                        {"type": "REPLY", "email_body": "not now thanks", "message_id": "m1"}]
    p = FakeProvider([{"label": "not_now", "reason": "later"}])
    assert [r["label"] for r in poll(sl, st, p, "900")] == ["not_now"]
    assert poll(sl, st, p, "900") == []
    counts = write_report(st, str(tmp_path / "r.md"))
    assert counts["not_now"] == 1 and "not_now" in (tmp_path / "r.md").read_text()


def test_strip_quoted():
    assert strip_quoted("Yes please\n> quoted\nOn Tue, Sam wrote:\nold") == "Yes please"


def test_webhook_server_token_and_events(st):
    server = serve("127.0.0.1", 0, "t" * 20, st, FakeProvider([]), FakeSmartlead())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/smartlead"

    def post(url, data):
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert post(base + "?token=wrong", {})[0] == 404
        code, body = post(base + "?token=" + "t" * 20, reply("unsubscribe"))
        assert code == 200 and body["label"] == "unsubscribe" and st.is_suppressed("jo@x.example.com")
        assert post(base + "?token=" + "t" * 20, [1])[0] == 400
    finally:
        server.shutdown()


def test_short_webhook_token_refused(st):
    with pytest.raises(ValueError):
        serve("127.0.0.1", 0, "short", st, None, None)


def test_catch_up_finishes_stop_requests(tmp_path):
    st = Store(str(tmp_path / "c.db"))
    st.set("campaign_id", "900")
    st.record_push("al@x.example.com", "900", None)  # Smartlead id not known yet
    sl = FakeSmartlead()
    sl.fail_block = True
    stop_everywhere(st, sl, "al@x.example.com", "asked", "reply")
    assert sl.unsubscribed == [] and sl.blocked == []
    with st.db:
        st.db.execute("UPDATE pushed SET lead_id='5007' WHERE email='al@x.example.com'")
    sl.fail_block = False
    assert catch_up(sl, st, "900") == ["al@x.example.com"]
    assert sl.unsubscribed == [5007] and sl.blocked == ["al@x.example.com"]
    assert catch_up(sl, st, "900") == [] and sl.unsubscribed == [5007]
