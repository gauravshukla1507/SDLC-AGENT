"""Routines: recurring work. A due routine becomes a new project, run like any other request.

Schedules (UTC, no external dependencies):
  @every <N>m|h|d         e.g. @every 6h
  @daily HH:MM            e.g. @daily 02:00
  @weekly <mon..sun> HH:MM
  @monthly <day> HH:MM    e.g. @monthly 1 03:00
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def is_due(schedule: str, last_run: str | None, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    last = _parse(last_run)
    s = schedule.strip().lower()
    if m := re.fullmatch(r"@every (\d+)([mhd])", s):
        delta = timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]: int(m.group(1))})
        return last is None or at - last >= delta
    if m := re.fullmatch(r"@daily (\d\d):(\d\d)", s):
        slot = at.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if slot > at:
            slot -= timedelta(days=1)
    elif m := re.fullmatch(r"@weekly (\w{3}) (\d\d):(\d\d)", s):
        slot = at.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        slot -= timedelta(days=(slot.weekday() - DAYS.index(m.group(1))) % 7)
        if slot > at:
            slot -= timedelta(days=7)
    elif m := re.fullmatch(r"@monthly (\d{1,2}) (\d\d):(\d\d)", s):
        slot = at.replace(day=int(m.group(1)), hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        if slot > at:
            prev = (slot.replace(day=1) - timedelta(days=1))
            slot = slot.replace(year=prev.year, month=prev.month)
    else:
        raise ValueError(f"Unsupported schedule: {schedule}")
    return last is None or last < slot


def due_routines(store, company_id: str, at: datetime | None = None) -> list[dict]:
    rows = store.all("SELECT * FROM routines WHERE company_id=? AND enabled=1", company_id)
    return [r for r in rows if is_due(r["schedule"], r["last_run"], at)]


def mark_run(store, routine_id: str, at: datetime | None = None):
    store.update("routines", "id", routine_id,
                 last_run=(at or datetime.now(timezone.utc)).isoformat(timespec="seconds"))
