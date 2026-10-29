"""Append-only local audit events. Never logs credentials or hidden reasoning."""

import json
import platform
import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .policy import now


def versions():
    result = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version}
    for package in ["langchain", "langgraph", "langchain-openai", "pydantic"]:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(public_paths(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Trace:
    def __init__(self, directory, mode):
        self.directory, self.mode = Path(directory), mode
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "trace.jsonl"
        if self.path.exists():
            raise DomainTraceError("运行目录已有 Trace；请使用新目录，避免覆盖证据")
        self.sequence = 0

    def emit(self, event, **fields):
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "timestamp": now(),
            "mode": self.mode,
            "event": event,
            **redact(fields),
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(public_paths(record), ensure_ascii=False) + "\n")


class DomainTraceError(ValueError):
    pass


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in {"confirmation_token", "token", "api_key"} and item else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def public_paths(value):
    """Normalize only workspace-prefixed transport paths, preserving all business facts."""
    if isinstance(value, dict):
        return {key: public_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_paths(item) for item in value]
    if isinstance(value, str):
        prefix = str(Path.cwd()) + "/"
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value
