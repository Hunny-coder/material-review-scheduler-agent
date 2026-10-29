"""Business tools. All durable mutations are guarded independently of model choices."""

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain.tools import tool

from .policy import (
    DomainError,
    contains,
    digest,
    dt,
    failure,
    iso,
    now,
    ok,
    original_window,
    overlaps,
    satisfies,
    search_allowed,
)
from .schemas import RulesConfig, ScheduleData


class Dataset:
    def __init__(self, directory):
        self.directory = Path(directory)

    def read(self, name):
        try:
            path = self.directory / f"{name}.json"
            if not path.is_file():
                raise OSError("资料必须为普通文件")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("资料必须是 JSON 对象")
            if name in {"materials", "supplements"} and any(
                not isinstance(value, dict) for value in payload.values()
            ):
                raise TypeError("素材和补充资料项必须为对象")
            if name == "rules":
                RulesConfig.model_validate(payload)
            elif name == "schedules":
                ScheduleData.model_validate(payload)
            return payload
        except (OSError, ValueError, TypeError) as exc:
            raise DomainError(f"{name.upper()}_UNAVAILABLE", f"无法读取合成资料 {name}") from exc

    def fingerprints(self):
        return {name: digest(self.read(name)) for name in ["materials", "supplements", "rules", "schedules"]}


class Runtime:
    def __init__(self, request, item, repository, dataset, mode="mock", fault=None, tentative_slots=None):
        self.request, self.item = request, item
        self.repo, self.dataset, self.mode = repository, dataset, mode
        self.fault = fault or {}
        self.tentative_slots = tentative_slots or []
        self.material = None
        self.supplemented = False
        self.review = {"status": "pending", "risk_reasons": [], "missing_fields": []}
        self.queries = []
        self.proposal = None
        self.receipt = None
        self.confirming = False
        self.last_error = None
        self.model_calls = self.tool_calls = 0
        self.final_status = None
        self.stop_reason = None
        self.read_tools = self._build_tools()
        self.tools = {entry.name: entry for entry in self.read_tools}

    @property
    def key(self):
        return self.repo.draft_key(self.request, self.item)

    def current_material(self):
        raw = self.dataset.read("materials").get(self.item["material_id"])
        if raw is None:
            raise DomainError("MATERIAL_NOT_FOUND", "未找到素材 " + self.item["material_id"])
        result = dict(raw)
        if self.supplemented:
            supplement = self.dataset.read("supplements").get(self.item["material_id"], {})
            # Only fill gaps. Supplemental data cannot overwrite populated metadata.
            result.update({key: value for key, value in supplement.items() if not result.get(key)})
        return result

    def schedule_snapshot(self, conn=None):
        external = self.dataset.read("schedules")
        occupied = [
            row
            for row in external["items"]
            if row["channel"] == self.item["channel"] and row["account_id"] == self.item["account_id"]
        ]
        local = self.repo.occupancy(self.item["channel"], self.item["account_id"], self.key, conn)
        revision = digest({"external": external, "local": sorted(local, key=lambda value: value["draft_id"])})
        return occupied + local, revision

    def evidence(self, conn=None):
        _, revision = self.schedule_snapshot(conn)
        return {
            "material_version": digest(self.current_material()),
            "rules_version": digest(self.dataset.read("rules")),
            "schedule_revision": revision,
            "supplemented": self.supplemented,
        }

    def validate_save(self, conn, effective_slot=None):
        proposal = self.proposal
        candidate = effective_slot or proposal["suggested_slot"]
        occupied, _ = self.schedule_snapshot(conn)
        for entry in occupied:
            if overlaps(candidate, entry):
                code = (
                    "MATERIAL_ALREADY_SCHEDULED"
                    if entry.get("material_id") == self.item["material_id"]
                    else "SLOT_CONFLICT"
                )
                raise DomainError(code, "保存时发现时段已被占用")
        if proposal["evidence"] != self.evidence(conn):
            raise DomainError("STALE_PROPOSAL", "素材、规则或排期版本已变化，请重新检查")
        if self.review["status"] != "passed":
            raise DomainError("RULES_BLOCKED", "审核未通过，禁止保存排期")

    def execute(self, name, args):
        try:
            if name not in self.tools:
                raise DomainError("INVALID_ACTION", "未知工具，禁止执行")
            if set(args) - set(self.tools[name].args):
                raise DomainError("INVALID_ACTION", "工具包含未经授权的额外参数")
            result = self.tools[name].invoke(args)
        except DomainError as exc:
            result = failure(exc)
        except sqlite3.Error:
            result = failure(DomainError("SAVE_FAILED", "数据库操作失败；未获得可确认的成功回执"))
        except (ValueError, TypeError, KeyError):
            result = failure(DomainError("INVALID_ACTION", "工具参数或资料结构不合法"))
        if not result["ok"]:
            self.last_error = result
        return result

    def _build_tools(self):
        @tool
        def read_material(material_id: str) -> dict:
            """读取当前素材元数据和缺失字段；必须使用输入中的素材 ID。"""
            self._same_material(material_id)
            self.supplemented = False
            self.material = self.current_material()
            self.review = {"status": "pending", "risk_reasons": [], "missing_fields": []}
            self.queries, self.proposal = [], None
            required = ["title", "summary", "channel", "license_id", "license_status"]
            return ok(
                {
                    "material": self.material,
                    "material_version": digest(self.material),
                    "missing_fields": [field for field in required if not self.material.get(field)],
                },
                "已读取合成素材元数据",
            )

        @tool
        def read_supplement(material_id: str, fields: list[str]) -> dict:
            """补查缺失字段。返回仍缺失字段；不能把未找到当作审核通过。"""
            self._same_material(material_id)
            if self.material is None:
                raise DomainError("MATERIAL_REQUIRED", "先读取素材")
            self.supplemented = True
            self.material = self.current_material()
            self.review = {"status": "pending", "risk_reasons": [], "missing_fields": []}
            self.proposal, self.queries = None, []
            return ok(
                {
                    "material_version": digest(self.material),
                    "found": {field: self.material[field] for field in fields if self.material.get(field)},
                    "still_missing": [field for field in fields if not self.material.get(field)],
                },
                "已补查可用合成资料",
            )

        @tool
        def check_rules(material_id: str, material_version: str, channel: str) -> dict:
            """使用已读取的真实元数据校验合成审核规则。风险必须附规则与字段证据。"""
            self._same_material(material_id)
            if channel != self.item["channel"] or self.material is None:
                raise DomainError("INVALID_ACTION", "渠道不符或尚未读取素材")
            if (
                material_version != digest(self.current_material())
                or self.material != self.current_material()
            ):
                raise DomainError("STALE_EVIDENCE", "素材版本已变化，请重新读取")
            rules = self.dataset.read("rules")
            missing = [field for field in rules["required_fields"] if not self.material.get(field)]
            risks = []
            for field in missing:
                risks.append(
                    {
                        "rule_id": "REQUIRED_FIELD",
                        "severity": "needs_info",
                        "field": field,
                        "reason": f"缺少必填字段 {field}",
                    }
                )
            for rule in rules["rules"]:
                value = self.material.get(rule["field"])
                matched = False
                if rule["operator"] == "in":
                    matched = value in rule["values"]
                elif rule["operator"] == "contains":
                    matched = any(word in str(value or "") for word in rule["values"])
                elif rule["operator"] == "max_length":
                    matched = len(str(value or "")) > rule["value"]
                if matched:
                    risks.append(
                        {
                            "rule_id": rule["id"],
                            "severity": rule["severity"],
                            "field": rule["field"],
                            "reason": rule["reason"],
                            "evidence": str(value)[:160],
                        }
                    )
            if self.material.get("channel") != channel:
                risks.append(
                    {
                        "rule_id": "CHANNEL_MATCH",
                        "severity": "blocked",
                        "field": "channel",
                        "reason": "素材渠道与请求渠道不一致",
                    }
                )
            severities = {risk["severity"] for risk in risks}
            status = next(
                (
                    candidate
                    for candidate in ["blocked", "needs_info", "needs_review"]
                    if candidate in severities
                ),
                "passed",
            )
            self.review = {
                "status": status,
                "missing_fields": missing,
                "risk_reasons": risks,
                "rules_version": digest(rules),
                "material_version": material_version,
            }
            existing = self.repo.get(draft_key=self.key)
            if (
                not self.confirming
                and existing
                and (status != "passed" or existing["payload"]["evidence"] != self.evidence())
            ):
                self.repo.invalidate(self.key, "素材、规则或排期证据变化")
            return ok(self.review, f"审核结果：{status}")

        @tool
        def query_slots(
            channel: str, account_id: str, start: str, end: str, duration_minutes: int = 30
        ) -> dict:
            """查询授权窗口内占用和最多三个空闲槽；替代搜索不代表允许更改硬要求。"""
            if channel != self.item["channel"] or account_id != self.item["account_id"]:
                raise DomainError("INVALID_ACTION", "不得查询其他渠道或账号")
            query = {"start": iso(dt(start)), "end": iso(dt(end))}
            if duration_minutes != 30 or not 0 < (dt(end) - dt(start)).total_seconds() <= 7 * 86400:
                raise DomainError("INVALID_ACTION", "查询仅支持 30 分钟槽及最多 7 天窗口")
            if not search_allowed(self.item, query):
                raise DomainError("OUTSIDE_AUTHORIZED_WINDOW", "查询超出用户授权范围")
            occupied, revision = self.schedule_snapshot()
            hours = self.dataset.read("rules").get("business_hours", [0, 24])
            free, pointer = [], dt(start)
            while pointer + timedelta(minutes=30) <= dt(end):
                candidate = {"start": iso(pointer), "end": iso(pointer + timedelta(minutes=30))}
                hour = pointer.astimezone(ZoneInfo("Asia/Shanghai")).hour
                if hours[0] <= hour < hours[1] and not any(overlaps(candidate, row) for row in occupied):
                    free.append(candidate)
                pointer += timedelta(minutes=30)
            preferred = self.item["publish_requirement"].get("at")
            free.sort(
                key=lambda candidate: (
                    any(overlaps(candidate, entry["slot"]) for entry in self.tentative_slots),
                    bool(preferred and dt(candidate["start"]) != dt(preferred)),
                    dt(candidate["start"]),
                )
            )
            result = {
                "query": query,
                "available_slots": free[:3],
                "occupied_slots": [entry for entry in occupied if overlaps(entry, query)],
                "schedule_revision": revision,
                "checked_at": now(),
            }
            self.queries.append(result)
            return ok(result, f"查询到 {len(free)} 个可用槽，返回前 3 个")

        @tool
        def propose_draft(material_id: str, candidate_start: str, candidate_end: str) -> dict:
            """从已查询的空闲槽登记提案，代码计算确认边界；不保存草稿。"""
            self._same_material(material_id)
            if self.review["status"] != "passed":
                raise DomainError("RULES_BLOCKED", "必须先完成审核且通过")
            candidate = {"start": iso(dt(candidate_start)), "end": iso(dt(candidate_end))}
            if not any(candidate in query["available_slots"] for query in self.queries):
                raise DomainError("INVALID_CANDIDATE", "候选不是已查询到的空闲时段")
            evidence = self.evidence()
            if (
                evidence["material_version"] != self.review["material_version"]
                or evidence["rules_version"] != self.review["rules_version"]
            ):
                raise DomainError("STALE_EVIDENCE", "审核资料变化，需重新审核")
            if not any(
                query["schedule_revision"] == evidence["schedule_revision"]
                and candidate in query["available_slots"]
                for query in self.queries
            ):
                raise DomainError("STALE_EVIDENCE", "排期变化，需重新查询")
            needs_confirmation = not satisfies(self.item, candidate)
            base = {
                "original_requirement": self.item["publish_requirement"],
                "suggested_slot": candidate,
                "effective_slot": None if needs_confirmation else candidate,
                "needs_confirmation": needs_confirmation,
                "evidence": evidence,
                "review": self.review,
                "item": self.item,
            }
            fingerprint = digest(base)
            existing = self.repo.get(draft_key=self.key)
            version = (
                1
                if not existing
                else existing["proposal_version"]
                + (existing["proposal_hash"] != fingerprint or existing["status"] == "invalidated")
            )
            self.proposal = {**base, "proposal_hash": fingerprint, "proposal_version": version}
            return ok(
                {
                    "proposal_version": version,
                    "expected_draft_version": existing["draft_version"] if existing else None,
                    "needs_confirmation": needs_confirmation,
                    "suggested_slot": candidate,
                },
                "已登记提案，尚未保存",
            )

        @tool
        def save_draft(
            material_id: str, proposal_version: int, expected_draft_version: int | None = None
        ) -> dict:
            """事务保存已登记提案；未确认的替代时间只能保存为待确认草稿。绝不发布。"""
            self._same_material(material_id)
            if not self.proposal or proposal_version != self.proposal["proposal_version"]:
                raise DomainError("STALE_PROPOSAL", "先登记有效提案")
            self.receipt = self.repo.save(
                self.request,
                self.item,
                self.mode,
                self.proposal,
                expected_draft_version,
                self.validate_save,
                self.fault.get("save"),
            )
            return ok(
                self.receipt, "草稿已保存" if self.receipt["status"] == "saved" else "已保存过，返回原草稿"
            )

        return [read_material, read_supplement, check_rules, query_slots, propose_draft, save_draft]

    def _same_material(self, material_id):
        if material_id != self.item["material_id"]:
            raise DomainError("INVALID_ACTION", "工具只能处理当前输入素材")

    def result(self):
        proposal = self.proposal or {}
        receipt = dict(self.receipt or {})
        persisted = self.repo.get(draft_key=self.key) if receipt or self.confirming else None
        if receipt and persisted:
            if persisted["status"] != "invalidated":
                try:
                    unchanged = persisted["payload"]["evidence"] == self.evidence()
                except DomainError:
                    unchanged = False
                if not unchanged:
                    self.repo.invalidate(self.key, "结束前发现证据变化")
                    persisted = self.repo.get(draft_key=self.key)
                    if self.review["status"] == "passed":
                        self.review = {"status": "pending", "risk_reasons": [], "missing_fields": []}
                    self.stop_reason = self.stop_reason or "STALE_EVIDENCE"
                elif self.review["status"] == "pending":
                    self.review = persisted["payload"]["review"]
            receipt["draft_status"] = persisted["status"]
            receipt["payload"] = persisted["payload"]
        status = self.review["status"]
        payload = receipt.get("payload", proposal)
        draft_status = persisted["status"] if persisted else receipt.get("draft_status")
        invalidated = draft_status == "invalidated"
        confirmation = payload.get(
            "confirmation_status", "pending" if proposal.get("needs_confirmation") else "not_required"
        )
        if invalidated and confirmation != "rejected":
            confirmation = "pending"
        suggested = payload.get("suggested_slot")
        original_queries = [
            query for query in self.queries if contains(original_window(self.item), query["query"])
        ]
        schedule_status = "unchecked"
        if self.queries:
            schedule_status = (
                "available"
                if original_queries and any(query["available_slots"] for query in original_queries)
                else "conflict"
            )
            if not any(query["available_slots"] for query in self.queries):
                schedule_status = "no_slot"
        save_status = receipt.get("status", "not_attempted")
        error = self.last_error
        if not receipt and error and error["code"] == "SAVE_FAILED":
            save_status = "failed"
        if not receipt and error and error["code"] == "SAVE_UNKNOWN":
            save_status = "unknown"
        pending = []
        if status == "needs_info":
            pending.append("请补充：" + "、".join(self.review["missing_fields"]))
        elif status == "needs_review":
            pending.append("请补充人工审核结果后重试")
        if invalidated:
            pending.append("草稿已失效，请重新运行审核与排期；旧确认不能继续使用")
        elif confirmation == "pending":
            pending.append("是否同意将原要求调整为 " + suggested["start"] + "？")
        if schedule_status == "no_slot":
            pending.append("授权范围内无空闲时段，请调整要求或扩大搜索窗口")
        if self.final_status is None:
            if status == "blocked":
                self.final_status = "blocked"
            elif status in {"needs_info", "needs_review"} or schedule_status == "no_slot":
                self.final_status = "awaiting_user"
            elif invalidated:
                self.final_status = "incomplete"
            elif receipt:
                self.final_status = "awaiting_user" if confirmation == "pending" else "completed"
            else:
                self.final_status = "failed" if save_status == "failed" else "incomplete"
        return {
            "material_id": self.item["material_id"],
            "run_status": self.final_status,
            "review_status": status,
            "risk_reasons": self.review["risk_reasons"],
            "missing_fields": self.review["missing_fields"],
            "schedule_status": schedule_status,
            "schedule_checked_at": self.queries[-1]["checked_at"] if self.queries else None,
            "original_requirement": self.item["publish_requirement"],
            "approved_requirement": payload.get("approved_requirement"),
            "suggested_slot": suggested,
            "effective_slot": payload.get("effective_slot") if receipt and not invalidated else None,
            "confirmation_status": confirmation,
            "pending_items": pending,
            "proposal_version": proposal.get("proposal_version"),
            "confirmation_token": None if invalidated else receipt.get("confirmation_token"),
            "draft_status": draft_status,
            "draft_save": {
                "status": save_status,
                "draft_id": receipt.get("draft_id") or (persisted["draft_id"] if persisted else None),
                "replayed": receipt.get("replayed", False),
                "error_code": error["code"] if error and not receipt else None,
            },
            "rules_version": self.review.get("rules_version"),
            "material_version": self.review.get("material_version"),
            "schedule_revision": self.queries[-1]["schedule_revision"] if self.queries else None,
            "stop_reason": self.stop_reason or (error["code"] if error and not receipt else None),
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "mutually_exclusive_with": [],
        }
