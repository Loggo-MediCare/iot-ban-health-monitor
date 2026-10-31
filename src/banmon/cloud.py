"""Cloud server with a relational database (chapter 5.2, 5.3.3).

Every reading is stored. WARNING alerts are pushed to the doctor's dashboard when they arrive.
CRITICAL alerts were already sent by SMS from the gateway, so the cloud only records their
delivery time. Readings that are not abnormal are stored without any notification, as in the
chapter's Fig. 5.2 workflow.
"""
from __future__ import annotations

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    patient INTEGER NOT NULL, t_min INTEGER NOT NULL, vital TEXT NOT NULL,
    value REAL NOT NULL, level INTEGER NOT NULL, received_t INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS readings_pv ON readings(patient, vital, t_min);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, patient INTEGER NOT NULL, vital TEXT NOT NULL, value REAL NOT NULL,
    level INTEGER NOT NULL, raised_t INTEGER NOT NULL, notified_t INTEGER,
    channel TEXT NOT NULL, received_t INTEGER NOT NULL);
"""


class CloudServer:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def ingest(self, received_t: int, body: str) -> None:
        msg = json.loads(body)
        pid = int(msg["patient"])
        self.db.executemany(
            "INSERT INTO readings VALUES (?,?,?,?,?,?)",
            [(pid, int(t), str(v), float(x), int(lv), received_t) for t, v, x, lv in msg["readings"]])
        for a in msg["alerts"]:
            critical = a["level"] == 2
            notified = a["notified_t"] if critical else received_t  # dashboard push on arrival
            self.db.execute(
                """INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     notified_t = COALESCE(alerts.notified_t, excluded.notified_t)""",
                (a["id"], pid, a["vital"], a["value"], a["level"], a["raised_t"], notified,
                 "sms:doctor+emergency+relative" if critical else "dashboard:doctor", received_t))
        self.db.commit()

    def alerts(self) -> list[dict]:
        cur = self.db.execute("SELECT id, patient, vital, value, level, raised_t, notified_t, channel "
                              "FROM alerts ORDER BY raised_t")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur]

    def reading_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def history(self, patient: int, vital: str, since_t: int = 0) -> list[tuple[int, float]]:
        """What the patient's smartphone app shows: recent values of one vital."""
        return self.db.execute("SELECT t_min, value FROM readings WHERE patient=? AND vital=? AND t_min>=? "
                               "ORDER BY t_min", (patient, vital, since_t)).fetchall()
