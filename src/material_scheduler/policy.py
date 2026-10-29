"""Deterministic constraints; no language-model response can grant permission."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .schemas import aware_time


class DomainError(Exception):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def dt(value):
    return aware_time(value).astimezone(UTC)


def iso(value):
    return value.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def slot(start):
    start = dt(start)
    return {"start": iso(start), "end": iso(start + timedelta(minutes=30))}


def overlaps(a, b):
    return dt(a["start"]) < dt(b["end"]) and dt(b["start"]) < dt(a["end"])


def contains(window, candidate):
    return dt(window["start"]) <= dt(candidate["start"]) and dt(candidate["end"]) <= dt(window["end"])


def original_window(item):
    requirement = item["publish_requirement"]
    if requirement["kind"] == "exact":
        return slot(requirement["at"])
    return {"start": requirement["start"], "end": requirement["end"]}


def satisfies(item, candidate):
    requirement = item["publish_requirement"]
    if requirement["kind"] == "exact":
        return candidate == slot(requirement["at"])
    return contains(original_window(item), candidate)


def search_allowed(item, query):
    windows = [original_window(item)]
    if item.get("alternative_search_window"):
        windows.append(item["alternative_search_window"])
    return any(contains(window, query) for window in windows)


def ok(data, summary):
    return {"ok": True, "code": "OK", "data": data, "summary": summary, "retryable": False}


def failure(error):
    return {
        "ok": False,
        "code": error.code,
        "data": {},
        "summary": error.message,
        "retryable": error.retryable,
    }
