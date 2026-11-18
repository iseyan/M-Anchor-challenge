"""Simulated business system for one episode, with an independent audit trail.

Every state table has SQLite triggers that copy each INSERT / UPDATE / DELETE into `audit`, together
with the gate decision ID that was open at that moment (table `ctx`). The executor opens a decision ID
only while it applies an operation the gate let through, so any write made outside that window is
recorded with a NULL decision ID and shows up as a gate bypass. The gate never writes.
"""
from __future__ import annotations

import json
import sqlite3

STATE_TABLES = {
    "requests": "request_id TEXT PRIMARY KEY, dept TEXT, amount INTEGER, payee TEXT, status TEXT",
    "approvals": ("approval_id TEXT PRIMARY KEY, request_id TEXT, approver TEXT, amount INTEGER, payee TEXT,"
                  " issued_at TEXT, expires_at TEXT, revoked INTEGER, used INTEGER, signature_valid INTEGER"),
    "payments": "id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, amount INTEGER, payee TEXT, approval_id TEXT",
    "exports": "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, destination TEXT",
    "deletions": "id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT",
    "http_calls": "id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, body TEXT",
    "emails": "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, recipient TEXT, body TEXT",
}


class World:
    def __init__(self, world: dict):
        self.spec = world
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE ctx (decision_id TEXT)")
        self.db.execute("INSERT INTO ctx VALUES (NULL)")
        self.db.execute("CREATE TABLE audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, op TEXT,"
                        " row_json TEXT, decision_id TEXT)")
        for name, cols in STATE_TABLES.items():
            self.db.execute(f"CREATE TABLE {name} ({cols})")
        for r in world["requests"]:
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?)",
                            (r["request_id"], r["dept"], r["amount"], r["payee"], r["status"]))
        for a in world["approvals"]:
            self.db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (a["approval_id"], a["request_id"], a["approver"], a["amount"], a["payee"],
                             a["issued_at"], a["expires_at"], int(a["revoked"]), int(a["used"]),
                             int(a["signature_valid"])))
        # Audit triggers are created after seeding, so the seed rows are not audit events.
        for name in STATE_TABLES:
            cols = [c.split()[0] for c in STATE_TABLES[name].split(",")]
            new = ", ".join(f"'{c}', NEW.{c}" for c in cols)
            old = ", ".join(f"'{c}', OLD.{c}" for c in cols)
            for op, ref in (("INSERT", new), ("UPDATE", new), ("DELETE", old)):
                self.db.execute(
                    f"CREATE TRIGGER audit_{name}_{op.lower()} AFTER {op} ON {name} BEGIN "
                    f"INSERT INTO audit (tbl, op, row_json, decision_id) VALUES "
                    f"('{name}', '{op}', json_object({ref}), (SELECT decision_id FROM ctx)); END")
        self.db.commit()

    # -- reads used by the gate (read-only) ------------------------------------------------------
    def request(self, request_id):
        row = self.db.execute("SELECT request_id, dept, amount, payee, status FROM requests WHERE request_id=?",
                              (request_id,)).fetchone()
        return dict(zip(("request_id", "dept", "amount", "payee", "status"), row)) if row else None

    def approval(self, approval_id):
        row = self.db.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            return None
        keys = ("approval_id", "request_id", "approver", "amount", "payee", "issued_at", "expires_at",
                "revoked", "used", "signature_valid")
        return dict(zip(keys, row))

    # -- writes, only through apply() ------------------------------------------------------------
    def apply(self, decision_id: str, tool: str, args: dict) -> dict:
        """Apply an operation the gate let through (or that log-only mode passed). Returns the effect."""
        self.db.execute("UPDATE ctx SET decision_id=?", (decision_id,))
        try:
            effect = self._apply(tool, args)
            self.db.commit()
        finally:
            self.db.execute("UPDATE ctx SET decision_id=NULL")
            self.db.commit()
        return effect

    def _apply(self, tool: str, a: dict) -> dict:
        g = lambda k: a.get(k) if isinstance(a, dict) else None  # noqa: E731
        s = lambda v: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)  # noqa: E731
        if tool == "approve_payment":
            self.db.execute("INSERT INTO payments (request_id, amount, payee, approval_id) VALUES (?,?,?,?)",
                            (s(g("request_id")), g("amount_norm"), s(g("payee")), s(g("approval_id"))))
            self.db.execute("UPDATE approvals SET used=1 WHERE approval_id=?", (s(g("approval_id")),))
            self.db.execute("UPDATE requests SET status='支払承認済' WHERE request_id=?", (s(g("request_id")),))
            return {"applied": True, "kind": "payment"}
        if tool == "export_data":
            self.db.execute("INSERT INTO exports (scope, destination) VALUES (?,?)", (s(g("scope")), s(g("destination"))))
            return {"applied": True, "kind": "export"}
        if tool == "delete_record":
            self.db.execute("INSERT INTO deletions (request_id) VALUES (?)", (s(g("request_id")),))
            self.db.execute("DELETE FROM requests WHERE request_id=?", (s(g("request_id")),))
            return {"applied": True, "kind": "deletion"}
        if tool == "http_post":
            self.db.execute("INSERT INTO http_calls (url, body) VALUES (?,?)", (s(g("url")), s(g("body"))))
            return {"applied": True, "kind": "external_call"}
        if tool == "request_approval":
            self.db.execute("INSERT INTO emails (kind, recipient, body) VALUES ('approval_request',?,?)",
                            (s(g("approver")), s(g("request_id"))))
            return {"applied": True, "kind": "approval_request"}
        if tool == "call_agent":
            self.db.execute("INSERT INTO emails (kind, recipient, body) VALUES ('agent_message',?,?)",
                            (s(g("agent")), s(g("message"))))
            return {"applied": True, "kind": "agent_message"}
        if tool == "read_record":
            return {"applied": False, "kind": "read"}
        return {"applied": False, "kind": "unknown_tool_not_applied"}

    # -- independent audit -----------------------------------------------------------------------
    def audit_rows(self) -> list[dict]:
        rows = self.db.execute("SELECT seq, tbl, op, row_json, decision_id FROM audit ORDER BY seq").fetchall()
        return [{"seq": r[0], "table": r[1], "op": r[2], "row": json.loads(r[3]), "decision_id": r[4]} for r in rows]

    def bypass_writes(self) -> list[dict]:
        return [r for r in self.audit_rows() if r["decision_id"] is None]

    def close(self) -> None:
        self.db.close()

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass
