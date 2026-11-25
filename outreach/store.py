"""Local SQLite store: suppression list, push ledger, replies, webhook events, Apollo credit log."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppression (
    email TEXT PRIMARY KEY, reason TEXT NOT NULL, source TEXT NOT NULL, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pushed (
    email TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, lead_id TEXT, pushed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS replies (
    email_id TEXT PRIMARY KEY, lead_email TEXT, subject TEXT, received_at TEXT,
    label TEXT, reason TEXT, snippet TEXT, classified_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (event_key TEXT PRIMARY KEY, event_type TEXT, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT, apollo_id TEXT, found INTEGER NOT NULL, used_at TEXT NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # suppression
    def suppress(self, email, reason, source):
        email = email.strip().lower()
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO suppression VALUES (?,?,?,?)", (email, reason, source, now()))
        return cur.rowcount == 1

    def unsuppress(self, email):
        with self.db:
            return self.db.execute("DELETE FROM suppression WHERE email=?",
                                   (email.strip().lower(),)).rowcount == 1

    def is_suppressed(self, email):
        return self.db.execute("SELECT 1 FROM suppression WHERE email=?",
                               (email.strip().lower(),)).fetchone() is not None

    def suppressed(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM suppression ORDER BY added_at")]

    # push ledger (duplicate protection)
    def was_pushed(self, email):
        return self.db.execute("SELECT 1 FROM pushed WHERE email=?",
                               (email.strip().lower(),)).fetchone() is not None

    def record_push(self, email, campaign_id, lead_id):
        with self.db:
            self.db.execute("INSERT INTO pushed VALUES (?,?,?,?)",
                            (email.strip().lower(), campaign_id, lead_id, now()))

    def pushed_on(self, day):
        """How many leads were pushed on a UTC day (YYYY-MM-DD): the local daily cap."""
        return self.db.execute("SELECT COUNT(*) FROM pushed WHERE substr(pushed_at,1,10)=?", (day,)).fetchone()[0]

    def lead_id_for(self, email):
        row = self.db.execute("SELECT lead_id FROM pushed WHERE email=?", (email.strip().lower(),)).fetchone()
        return row["lead_id"] if row else None

    def pushed(self, campaign_id=None):
        sql, args = "SELECT * FROM pushed", ()
        if campaign_id:
            sql, args = sql + " WHERE campaign_id=?", (campaign_id,)
        return [dict(r) for r in self.db.execute(sql, args)]

    def forget_push(self, email):
        with self.db:
            self.db.execute("DELETE FROM pushed WHERE email=?", (email.strip().lower(),))

    # replies
    def reply_seen(self, email_id):
        return self.db.execute("SELECT 1 FROM replies WHERE email_id=?", (email_id,)).fetchone() is not None

    def record_reply(self, email_id, lead_email, subject, received_at, label, reason, snippet):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?,?,?,?)",
                            (email_id, lead_email, subject, received_at, label, reason, snippet, now()))

    def replies(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM replies ORDER BY received_at")]

    # webhook events (each delivery handled once, even if Smartlead retries it)
    def event_seen(self, key):
        return self.db.execute("SELECT 1 FROM events WHERE event_key=?", (key,)).fetchone() is not None

    def record_event(self, key, event_type):
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)", (key, event_type, now()))
        return cur.rowcount == 1

    # Apollo enrichment log (each call may cost a credit)
    def record_credit(self, apollo_id, found):
        with self.db:
            self.db.execute("INSERT INTO credits (apollo_id, found, used_at) VALUES (?,?,?)",
                            (apollo_id, 1 if found else 0, now()))

    def credits_used(self, day=None):
        sql, args = "SELECT COUNT(*) FROM credits WHERE found=1", ()
        if day:
            sql, args = sql + " AND substr(used_at,1,10)=?", (day,)
        return self.db.execute(sql, args).fetchone()[0]

    def enriched_before(self, apollo_id):
        return self.db.execute("SELECT 1 FROM credits WHERE apollo_id=?", (apollo_id,)).fetchone() is not None

    # state
    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key, value):
        with self.db:
            if value is None:
                self.db.execute("DELETE FROM state WHERE key=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, str(value)))
