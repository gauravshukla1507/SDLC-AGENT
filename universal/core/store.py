"""Durable state for the Universal Orchestrator (SQLite; swap for Postgres in production).

Everything a company does lives here: org chart, goals, tasks, runs, costs, approvals,
work products and an append-only activity log. Task checkout is atomic, so two agents
(or two orchestrator processes) can never work the same task at once.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, mission TEXT, spec TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, company_id TEXT NOT NULL, key TEXT NOT NULL, name TEXT, title TEXT,
  reports_to TEXT, adapter TEXT, status TEXT, budget_usd REAL, spent_usd REAL DEFAULT 0,
  skills TEXT, config TEXT, UNIQUE(company_id, key));
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, company_id TEXT NOT NULL, parent_id TEXT, level TEXT, title TEXT,
  description TEXT, budget_usd REAL, spent_usd REAL DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, company_id TEXT NOT NULL, goal_id TEXT, parent_id TEXT, key TEXT,
  title TEXT, kind TEXT, assignee TEXT, status TEXT, deps TEXT, checked_out_by TEXT,
  attempts INTEGER DEFAULT 0, spec TEXT, result TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, company_id TEXT, task_id TEXT, agent_key TEXT, adapter TEXT, model TEXT,
  started TEXT, finished TEXT, status TEXT, input_tokens INTEGER, output_tokens INTEGER,
  tokens_estimated INTEGER DEFAULT 0, cost_usd REAL, summary TEXT);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, company_id TEXT, type TEXT, subject TEXT, task_id TEXT, payload TEXT,
  status TEXT, decided_by TEXT, comment TEXT, created TEXT, decided TEXT);
CREATE TABLE IF NOT EXISTS work_products (
  id TEXT PRIMARY KEY, company_id TEXT, task_id TEXT, path TEXT, physical_path TEXT,
  content TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS activity (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, ts TEXT, actor TEXT, action TEXT,
  entity TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS routines (
  id TEXT PRIMARY KEY, company_id TEXT, key TEXT, schedule TEXT, spec TEXT, last_run TEXT,
  enabled INTEGER DEFAULT 1, UNIQUE(company_id, key));
"""

JSON_COLS = {"skills", "config", "deps", "spec", "result", "payload", "details"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ generic helpers
    @staticmethod
    def _enc(row: dict) -> dict:
        return {k: (json.dumps(v) if k in JSON_COLS and v is not None and not isinstance(v, str) else v)
                for k, v in row.items()}

    @staticmethod
    def _dec(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for k in JSON_COLS & out.keys():
            if isinstance(out[k], str):
                try:
                    out[k] = json.loads(out[k])
                except json.JSONDecodeError:
                    pass
        return out

    def insert(self, table: str, row: dict) -> dict:
        row = self._enc(row)
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        with self.lock:
            self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))
        return row

    def update(self, table: str, key: str, value, **fields):
        fields = self._enc(fields)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", (*fields.values(), value))

    def one(self, sql: str, *args) -> dict | None:
        with self.lock:
            return self._dec(self.db.execute(sql, args).fetchone())

    def all(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [self._dec(r) for r in self.db.execute(sql, args).fetchall()]

    # ------------------------------------------------------------ activity log
    def log(self, company_id: str, actor: str, action: str, entity: str = "", **details):
        with self.lock:
            self.db.execute("INSERT INTO activity (company_id, ts, actor, action, entity, details) VALUES (?,?,?,?,?,?)",
                            (company_id, now(), actor, action, entity, json.dumps(details, default=str)))

    # ------------------------------------------------------------ tasks
    def checkout(self, task_id: str, agent_key: str) -> bool:
        """Atomic checkout: only one worker can move a task from todo to in_progress."""
        with self.lock:
            cur = self.db.execute(
                "UPDATE tasks SET status='in_progress', checked_out_by=?, updated=? "
                "WHERE id=? AND status='todo' AND checked_out_by IS NULL", (agent_key, now(), task_id))
            return cur.rowcount == 1

    def release(self, task_id: str, status: str, result: dict | None = None, **extra):
        self.update("tasks", "id", task_id, status=status, checked_out_by=None, updated=now(),
                    **({"result": result} if result is not None else {}), **extra)
