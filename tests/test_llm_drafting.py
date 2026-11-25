from types import SimpleNamespace

import pytest

from outreach.drafting import Drafter, DraftError, parse_json
from outreach.llm import BudgetExceeded, ClaudeProvider, CostGuard, LLMError

from .conftest import FakeProvider, good_draft


def test_clean_first_time(lead, settings):
    p = FakeProvider([good_draft()])
    draft, check = Drafter(p, settings).draft(lead)
    assert check.clean and len(p.calls) == 1
    assert "Riverside Bakery" in p.calls[0]["user"] and "jo@bakery" not in p.calls[0]["user"]


def test_retry_feeds_back_problems(lead, settings):
    p = FakeProvider([good_draft(subject_1="x" * 80), good_draft()])
    draft, check = Drafter(p, settings).draft(lead)
    assert check.clean and "failed these checks" in p.calls[1]["user"]


def test_bad_json_twice_gives_error(lead, settings):
    p = FakeProvider(["not json", "still not"])
    draft, check = Drafter(p, settings).draft(lead)
    assert draft is None and not check.ok


def test_fenced_json_parses():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(DraftError):
        parse_json("[1]")


def test_cost_guard_stops_by_calls(lead, settings):
    guard = CostGuard(max_calls=1, max_usd=10)
    p = FakeProvider([good_draft(subject_1="x" * 80), good_draft()], guard=guard)
    with pytest.raises(BudgetExceeded):
        Drafter(p, settings).draft(lead)
    assert guard.calls == 1


def test_cost_guard_stops_by_dollars():
    guard = CostGuard(max_calls=100, max_usd=0.01)
    guard.usd = 0.0
    with pytest.raises(BudgetExceeded):
        guard.check(est_output_tokens=2000)  # 2000 * $10/M = $0.02 > $0.01


def _msg(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


class FakeAnthropic:
    def __init__(self, msg=None, exc=None):
        self.msg, self.exc, self.kwargs = msg, exc, None
        self.messages = self

    def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.msg


def test_claude_provider_reads_text_and_usage():
    guard = CostGuard(5, 1.0)
    c = FakeAnthropic(_msg("hello"))
    out = ClaudeProvider(client=c, guard=guard).complete("sys", "user", 100)
    assert out.text == "hello" and c.kwargs["model"] == "claude-sonnet-5-5"
    assert guard.calls == 1 and guard.usd == pytest.approx((1000 * 2 + 200 * 10) / 1e6)


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_claude_provider_bad_stops(stop):
    with pytest.raises(LLMError):
        ClaudeProvider(client=FakeAnthropic(_msg("x", stop))).complete("s", "u")


def test_claude_provider_wraps_sdk_errors():
    with pytest.raises(LLMError, match="RuntimeError"):
        ClaudeProvider(client=FakeAnthropic(exc=RuntimeError("boom"))).complete("s", "u")
