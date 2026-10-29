"""SQLite owns write facts, concurrency control and confirmation history."""

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .policy import DomainError, canonical, digest, now


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    request_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_key TEXT UNIQUE NOT NULL, draft_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL, material_id TEXT NOT NULL,
                    channel TEXT NOT NULL, account_id TEXT NOT NULL,
                    mode TEXT NOT NULL, proposal_version INTEGER NOT NULL,
                    draft_version INTEGER NOT NULL, proposal_hash TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, token_hash TEXT,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    operation_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                    draft_id TEXT NOT NULL, committed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS confirmation_events (
                    event_id INTEGER PRIMARY KEY, draft_id TEXT NOT NULL,
                    proposal_version INTEGER NOT NULL, decision TEXT NOT NULL,
                    created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def request_key(request):
        return canonical([request["request_id"], request["request_revision"]])

    @classmethod
    def draft_key(cls, request, item):
        return digest([cls.request_key(request), item["material_id"], item["channel"], item["account_id"]])

    def register_request(self, request):
        key, fingerprint = self.request_key(request), digest(request)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM requests WHERE request_key=?", (key,)).fetchone()
            if previous and previous["input_hash"] != fingerprint:
                conn.rollback()
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同请求标识对应不同输入，请创建新修订")
            conn.execute(
                "INSERT OR IGNORE INTO requests VALUES (?, ?, ?)", (key, fingerprint, canonical(request))
            )
            conn.commit()

    def get(self, draft_id=None, draft_key=None, conn=None):
        if conn is None:
            with self.connection() as connection:
                return self.get(draft_id, draft_key, connection)
        column, value = ("draft_id", draft_id) if draft_id else ("draft_key", draft_key)
        row = conn.execute(f"SELECT * FROM drafts WHERE {column}=?", (value,)).fetchone()
        if not row:
            return None
        return {**dict(row), "payload": json.loads(row["payload"])}

    def request_for(self, draft):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM requests WHERE request_key=?", (draft["request_key"],)
            ).fetchone()
            return json.loads(row["payload"])

    def occupancy(self, channel, account_id, exclude_key=None, conn=None):
        if conn is None:
            with self.connection() as connection:
                return self.occupancy(channel, account_id, exclude_key, connection)
        rows = conn.execute(
            "SELECT * FROM drafts WHERE channel=? AND account_id=? AND status='confirmed'",
            (channel, account_id),
        ).fetchall()
        return [
            {
                "draft_id": row["draft_id"],
                "material_id": row["material_id"],
                **json.loads(row["payload"])["effective_slot"],
            }
            for row in rows
            if row["draft_key"] != exclude_key
        ]

    def count(self):
        with self.connection() as conn:
            return conn.execute("SELECT count(*) FROM drafts").fetchone()[0]

    def invalidate(self, draft_key, reason):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE drafts SET status='invalidated', draft_version=draft_version+1, updated_at=? "
                "WHERE draft_key=? AND status!='invalidated'",
                (now(), draft_key),
            )
            conn.commit()

    def receipt(self, draft, *, status="already_saved", token=None):
        return {
            "status": status,
            "draft_id": draft["draft_id"],
            "draft_version": draft["draft_version"],
            "draft_status": draft["status"],
            "confirmation_token": token,
            "payload": draft["payload"],
            "committed_at": draft["updated_at"],
            "replayed": status == "already_saved",
        }

    def save(self, request, item, mode, proposal, expected_version, validate, fault=None):
        key = self.draft_key(request, item)
        operation = canonical([key, proposal["proposal_version"], "save"])
        payload_hash = digest(proposal)
        token = None
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                existing = self.get(draft_key=key, conn=conn)
                prior = conn.execute("SELECT * FROM receipts WHERE operation_key=?", (operation,)).fetchone()
                if existing and existing["mode"] != mode:
                    raise DomainError("MODE_MISMATCH", "同一请求不可混用 live 与 mock 数据库记录")
                if prior:
                    if prior["payload_hash"] != payload_hash:
                        raise DomainError("IDEMPOTENCY_CONFLICT", "相同保存操作对应不同提案")
                    if (
                        existing["proposal_hash"] == proposal["proposal_hash"]
                        and existing["status"] != "invalidated"
                    ):
                        # Historical receipt is not proof of current availability. Revalidate even on replay.
                        validate(conn, existing["payload"].get("effective_slot"))
                        conn.commit()
                        return self.receipt(existing)
                if existing and existing["draft_version"] != expected_version:
                    raise DomainError("STALE_PROPOSAL", "草稿版本已变化，请重新生成提案")
                validate(conn, proposal.get("effective_slot"))
                if fault == "before_save":
                    raise DomainError("SAVE_FAILED", "注入故障：保存前失败")
                status = "pending_confirmation" if proposal["needs_confirmation"] else "confirmed"
                draft_id = existing["draft_id"] if existing else "D-" + uuid4().hex[:16]
                draft_version = existing["draft_version"] + 1 if existing else 1
                token_hash = None
                if status == "pending_confirmation":
                    token = secrets.token_urlsafe(24)
                    token_hash = digest(token)
                payload = {**proposal, "confirmation_status": "pending" if token else "not_required"}
                conn.execute(
                    """INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_key) DO UPDATE SET
                    proposal_version=excluded.proposal_version, draft_version=excluded.draft_version,
                    proposal_hash=excluded.proposal_hash, status=excluded.status,
                    payload=excluded.payload, token_hash=excluded.token_hash, updated_at=excluded.updated_at""",
                    (
                        key,
                        draft_id,
                        self.request_key(request),
                        item["material_id"],
                        item["channel"],
                        item["account_id"],
                        mode,
                        proposal["proposal_version"],
                        draft_version,
                        proposal["proposal_hash"],
                        status,
                        canonical(payload),
                        token_hash,
                        now(),
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO receipts VALUES (?, ?, ?, ?)",
                    (operation, payload_hash, draft_id, now()),
                )
                if fault == "before_commit":
                    raise DomainError("SAVE_FAILED", "注入故障：写入后、提交前失败，事务回滚")
                conn.commit()
                committed = True
                if fault == "after_commit_unreadable":
                    raise DomainError("SAVE_UNKNOWN", "提交后回执不可核验，请恢复后用原请求查询，勿新建请求")
                committed = self.get(draft_id, conn=conn)
                # A lost post-commit response is reconciled against committed database facts.
                return self.receipt(
                    committed, status="already_saved" if fault == "after_commit" else "saved", token=token
                )
            except sqlite3.Error as exc:
                # After a commit, losing access to the receipt must never be reported as a rollback.
                if committed or not conn.in_transaction:
                    raise DomainError("SAVE_UNKNOWN", "无法确认事务提交结果，请恢复后用原请求核验") from exc
                conn.rollback()
                raise DomainError("SAVE_FAILED", "保存事务失败并已回滚") from exc
            except Exception:
                conn.rollback()
                raise

    def confirm(self, draft_id, proposal_version, token, mode, decision, validate):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                draft = self.get(draft_id, conn=conn)
                if not draft:
                    raise DomainError("DRAFT_NOT_FOUND", "草稿不存在")
                if draft["mode"] != mode:
                    raise DomainError("MODE_MISMATCH", "确认模式必须与草稿模式相同")
                if draft["proposal_version"] != proposal_version or not draft["token_hash"]:
                    raise DomainError("STALE_PROPOSAL", "提案版本过期或无需确认")
                if not secrets.compare_digest(draft["token_hash"], digest(token)):
                    raise DomainError("INVALID_CONFIRMATION", "确认凭据不匹配")
                replay = draft["status"] == "confirmed" and decision == "approve"
                if draft["status"] != "pending_confirmation" and not replay:
                    raise DomainError("STALE_PROPOSAL", "提案已失效或被拒绝")
                if decision == "approve":
                    try:
                        validate(conn, draft)
                    except DomainError as exc:
                        if exc.code in {"STALE_PROPOSAL", "SLOT_CONFLICT", "RULES_BLOCKED"}:
                            conn.execute(
                                "UPDATE drafts SET status='invalidated', updated_at=? WHERE draft_id=?",
                                (now(), draft_id),
                            )
                            conn.commit()
                        raise
                if replay:
                    conn.commit()
                    return self.receipt(draft)
                payload = draft["payload"]
                payload["confirmation_status"] = "confirmed" if decision == "approve" else "rejected"
                payload["effective_slot"] = payload["suggested_slot"] if decision == "approve" else None
                payload["approved_requirement"] = payload["suggested_slot"] if decision == "approve" else None
                conn.execute(
                    "UPDATE drafts SET status=?, payload=?, draft_version=draft_version+1, updated_at=? WHERE draft_id=?",
                    (
                        "confirmed" if decision == "approve" else "invalidated",
                        canonical(payload),
                        now(),
                        draft_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO confirmation_events(draft_id,proposal_version,decision,created_at) VALUES(?,?,?,?)",
                    (draft_id, proposal_version, decision, now()),
                )
                conn.commit()
                return self.receipt(self.get(draft_id, conn=conn), status="saved")
            except Exception:
                conn.rollback()
                raise
