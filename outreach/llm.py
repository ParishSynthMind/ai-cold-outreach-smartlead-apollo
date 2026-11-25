"""Model provider abstraction and the cost guard.

Drafting and reply sorting only need "send a system prompt and one user message, get text
back", so that is the whole interface. ClaudeProvider is the real one; tests use a fake.
Another provider only has to implement complete().
"""
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
# US dollars per million tokens, Anthropic list prices for claude-sonnet-5-5 (Nov 2026).
# Used only to ESTIMATE spend for the guard; check the pricing page and adjust if they change.
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"


class CostGuard:
    """Stops a run before it can spend more than the configured calls or dollars."""

    def __init__(self, max_calls, max_usd, model=MODEL):
        self.max_calls, self.max_usd = int(max_calls), float(max_usd)
        self.price_in, self.price_out = PRICES.get(model, PRICES[MODEL])
        self.calls, self.usd = 0, 0.0

    def check(self, est_output_tokens=1500):
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"cost guard: {self.calls} model calls used (limit {self.max_calls})")
        # assume the next call could use this much output: never start a call that could cross the line
        worst = self.usd + est_output_tokens * self.price_out / 1e6
        if worst > self.max_usd:
            raise BudgetExceeded(f"cost guard: about ${self.usd:.4f} spent, next call could pass ${self.max_usd:.2f}")

    def record(self, completion):
        self.calls += 1
        self.usd += (completion.input_tokens * self.price_in + completion.output_tokens * self.price_out) / 1e6

    def summary(self):
        return f"{self.calls} model calls, about ${self.usd:.4f} (limit {self.max_calls} calls / ${self.max_usd:.2f})"


class ClaudeProvider:
    def __init__(self, client=None, model=MODEL, guard=None):
        if client is None:
            import anthropic

            from .secrets import get_secret
            client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY", "anthropic-api-key"))
        self.client, self.model, self.guard = client, model, guard

    def complete(self, system, user, max_tokens=4000):
        if self.guard:
            self.guard.check()
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # the SDK's own errors; keep the type name, never the request
            raise LLMError(f"model call failed: {type(exc).__name__}: {exc}") from None
        usage = getattr(msg, "usage", None)
        out = Completion(
            text="".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "text") == "text"),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            stop_reason=getattr(msg, "stop_reason", "end_turn"),
        )
        if self.guard:
            self.guard.record(out)
        if out.stop_reason == "max_tokens":
            raise LLMError("model reply was cut off (max_tokens)")
        if out.stop_reason == "refusal":
            raise LLMError("model declined this request")
        return out
