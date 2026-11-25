"""Small shared HTTP layer: retries on 429 and 5xx, honours Retry-After, never leaks the key."""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)

USER_AGENT = "ai-cold-outreach-smartlead-apollo/1.0 (+https://github.com/ParishSynthMind)"
RETRY_STATUS = (429, 500, 502, 503, 504)


class ApiError(Exception):
    def __init__(self, service, status, message, path):
        self.service, self.status, self.message, self.path = service, status, message, path
        super().__init__(f"{service} {path}: HTTP {status}: {message}")

    @property
    def is_plan_limit(self):
        text = (self.message or "").lower()
        return self.status in (402, 403) and any(w in text for w in ("plan", "upgrade", "credit", "paid"))


def redact(text, secret):
    text = str(text or "")
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"(api_key=)[^&\s\"']+", r"\1***", text)


class HttpClient:
    service = "api"

    def __init__(self, api_key, base_url, session=None, max_retries=3, backoff=1.0, timeout=30, sleep=time.sleep):
        if not api_key:
            raise ValueError(f"{self.service} api key is empty")
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self._sleep = sleep

    def __repr__(self):
        return f"<{type(self).__name__} base_url={self.base_url!r} key=***>"

    # subclasses add auth here
    def _auth(self, headers, params):
        return headers, params

    def request(self, method, path, json=None, params=None):
        url = self.base_url + path
        headers, params = self._auth({}, dict(params or {}))
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.request(method, url, json=json, params=params, headers=headers,
                                            timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise ApiError(self.service, 0, redact(exc, self._key), path) from None
                self._sleep(self.backoff * 2 ** (attempt - 1))
                continue
            if resp.status_code in RETRY_STATUS and attempt <= self.max_retries:
                wait = resp.headers.get("Retry-After")
                try:
                    delay = float(wait) if wait else self.backoff * 2 ** (attempt - 1)
                except ValueError:
                    delay = self.backoff * 2 ** (attempt - 1)
                log.info("%s %s -> %s, retry %s in %.1fs", self.service, path, resp.status_code, attempt, delay)
                self._sleep(min(delay, 60))
                continue
            if resp.status_code >= 400:
                raise ApiError(self.service, resp.status_code, redact(resp.text[:500], self._key), path)
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                raise ApiError(self.service, resp.status_code, "response was not JSON", path) from None
