"""Application entry points shared by CLI, tests and reproducible examples."""

import os
import subprocess
from pathlib import Path

from pydantic import ValidationError

from .graph import run_item
from .policy import DomainError, digest, failure, now, overlaps
from .repository import Repository
from .schemas import MaterialItem, ScheduleRequest
from .tools import Dataset, Runtime
from .tracing import Trace, versions, write_json

EXIT_CODES = {"completed": 0, "awaiting_user": 2, "blocked": 3, "failed": 4, "incomplete": 6}


def overall(items):
    for status in ["failed", "incomplete", "blocked", "awaiting_user", "completed"]:
        if any(item["run_status"] == status for item in items):
            return status
    return "incomplete"


def code_for(output):
    codes = [entry.get("stop_reason") for entry in output.get("items", [])]
    codes += [entry.get("code") for entry in output.get("errors", [])]
    if "MODEL_UNAVAILABLE" in codes:
        return 7
    if "INVALID_INPUT" in codes or "IDEMPOTENCY_CONFLICT" in codes:
        return 5
    return EXIT_CODES[output["run_status"]]


def source_identity():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=2
        ).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True, timeout=2).strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
    root = Path(__file__).parent
    return {
        "code_commit": commit,
        "working_tree_dirty": dirty,
        "source_hash": digest(
            {path.name: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))}
        ),
    }


def finish_run(output, trace, started, dataset, repo, initial_count, model_name=None):
    output["trace_file"] = str(trace.path)
    output["exit_code"] = code_for(output)
    trace.emit("run_end", run_status=output["run_status"], exit_code=output["exit_code"])
    write_json(trace.directory / "output.json", output)
    try:
        data_fingerprints = dataset.fingerprints()
    except DomainError:
        data_fingerprints = {"error": "部分资料不可读取，详见工具观察"}
    metadata = {
        "started_at": started,
        "finished_at": now(),
        "mode": trace.mode,
        "model": model_name
        or ("offline-script-v1" if trace.mode == "mock" else os.getenv("MODEL_NAME", "unconfigured")),
        "versions": versions(),
        "data_fingerprints": data_fingerprints,
        "database_initial_draft_count": initial_count,
        "database_final_draft_count": repo.count(),
        "exit_code": output["exit_code"],
        **source_identity(),
    }
    write_json(trace.directory / "run-metadata.json", metadata)
    write_json(
        trace.directory / "assertions.json",
        {
            "publication_status": output["publication_status"],
            "draft_count": repo.count(),
            "pending_has_no_effective_slot": all(
                item.get("effective_slot") is None
                for item in output["items"]
                if item.get("confirmation_status") == "pending"
            ),
            "saved_items_have_draft_id": all(
                item["draft_save"].get("draft_id")
                for item in output["items"]
                if item.get("draft_save", {}).get("status") in {"saved", "already_saved"}
            ),
        },
    )
    return output


def run_request(
    raw,
    *,
    db="var/review.sqlite3",
    data_dir="data",
    run_dir="var/runs/latest",
    mode="mock",
    max_model_calls=12,
    max_tool_calls=12,
    fault=None,
    model_factory=None,
):
    if mode not in {"mock", "live"}:
        raise ValueError("mode 必须为 mock 或 live")
    if not 1 <= max_model_calls <= 60 or not 1 <= max_tool_calls <= 60:
        raise ValueError("调用上限必须为 1—60")
    started = now()
    trace = Trace(run_dir, mode)
    write_json(trace.directory / "input.json", raw)
    dataset, repo = Dataset(data_dir), Repository(db)
    count = repo.count()
    request_id = raw.get("request_id") if isinstance(raw, dict) else None
    output = {
        "request_id": request_id,
        "mode": mode,
        "run_status": "incomplete",
        "publication_status": "not_published",
        "items": [],
        "errors": [],
        "notices": [],
    }
    trace.emit(
        "run_start",
        request_id=request_id,
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        data_directory=str(data_dir),
    )
    try:
        request = ScheduleRequest.model_validate(raw).model_dump(mode="json", exclude_none=True)
        repo.register_request(request)
    except (ValidationError, ValueError, KeyError) as exc:
        output["errors"].append({"code": "INVALID_INPUT", "summary": "输入不合法：" + str(exc)[:800]})
        output["run_status"] = "failed"
        return finish_run(output, trace, started, dataset, repo, count)
    except DomainError as exc:
        output["errors"].append({"code": exc.code, "summary": exc.message})
        output["run_status"] = "failed"
        return finish_run(output, trace, started, dataset, repo, count)
    seen = {}
    tentative_slots = []
    valid_results = []
    batch_budget = {"model": 60, "tool": 60}
    for index, raw_item in enumerate(request["items"]):
        try:
            item = MaterialItem.model_validate(raw_item).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            output["items"].append(
                {
                    "material_id": raw_item.get("material_id"),
                    "run_status": "failed",
                    "stop_reason": "INVALID_INPUT",
                    "input_errors": exc.errors(include_input=False, include_context=False),
                }
            )
            continue
        identity = (item["material_id"], item["channel"], item["account_id"])
        if identity in seen:
            if seen[identity] == item:
                output["notices"].append(f"输入第 {index + 1} 项重复，已规范化")
            else:
                output["items"].append(
                    {
                        "material_id": item["material_id"],
                        "run_status": "failed",
                        "stop_reason": "INVALID_INPUT",
                        "input_errors": "重复素材含不一致要求",
                    }
                )
            continue
        seen[identity] = item
        in_scope = [
            entry
            for entry in tentative_slots
            if entry["channel"] == item["channel"] and entry["account_id"] == item["account_id"]
        ]
        runtime = Runtime(request, item, repo, dataset, mode, fault, in_scope)
        result = run_item(
            runtime,
            trace,
            max_model_calls,
            max_tool_calls,
            model=model_factory(runtime) if model_factory else None,
            batch_budget=batch_budget,
        )
        result["evidence_trace"] = str(trace.path)
        output["items"].append(result)
        valid_results.append((item, result))
        if result.get("suggested_slot") and result["confirmation_status"] == "pending":
            tentative_slots.append(
                {
                    "slot": result["suggested_slot"],
                    "material_id": item["material_id"],
                    "channel": item["channel"],
                    "account_id": item["account_id"],
                }
            )
    for index, (left_item, left) in enumerate(valid_results):
        for right_item, right in valid_results[index + 1 :]:
            if (left_item["channel"], left_item["account_id"]) != (
                right_item["channel"],
                right_item["account_id"],
            ):
                continue
            if (
                left.get("suggested_slot")
                and right.get("suggested_slot")
                and "pending" in {left["confirmation_status"], right["confirmation_status"]}
                and overlaps(left["suggested_slot"], right["suggested_slot"])
            ):
                left["mutually_exclusive_with"].append(right_item["material_id"])
                right["mutually_exclusive_with"].append(left_item["material_id"])
                for result in [left, right]:
                    result["pending_items"].append(
                        "本批次候选与其他素材互斥，不能同时确认；确认前必须重新核对"
                    )
    output["run_status"] = overall(output["items"])
    return finish_run(output, trace, started, dataset, repo, count)


def confirm_draft(
    *,
    draft_id,
    proposal_version,
    token,
    decision="approve",
    db="var/review.sqlite3",
    data_dir="data",
    run_dir="var/runs/confirm",
    mode="mock",
):
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 必须为 approve 或 reject")
    started, trace = now(), Trace(run_dir, mode)
    repo, dataset = Repository(db), Dataset(data_dir)
    count = repo.count()
    write_json(
        trace.directory / "input.json",
        {
            "draft_id": draft_id,
            "proposal_version": proposal_version,
            "token": "[redacted]",
            "decision": decision,
            "mode": mode,
        },
    )
    output = {
        "request_id": None,
        "mode": mode,
        "run_status": "incomplete",
        "publication_status": "not_published",
        "items": [],
        "errors": [],
    }
    draft = repo.get(draft_id)
    if not draft:
        output["errors"] = [{"code": "DRAFT_NOT_FOUND", "summary": "草稿不存在"}]
        output["run_status"] = "failed"
        return finish_run(output, trace, started, dataset, repo, count)
    request, item = repo.request_for(draft), draft["payload"]["item"]
    output["request_id"] = request["request_id"]
    runtime = Runtime(request, item, repo, dataset, mode)
    runtime.confirming = True
    runtime.supplemented = draft["payload"]["evidence"]["supplemented"]
    runtime.proposal = draft["payload"]

    def validate(conn, current):
        runtime.material = runtime.current_material()
        check = runtime.execute(
            "check_rules",
            {
                "material_id": item["material_id"],
                "material_version": digest(runtime.material),
                "channel": item["channel"],
            },
        )
        trace.emit(
            "confirmation_guard", action_source="confirmation_guard", tool_name="check_rules", result=check
        )
        if not check["ok"] or runtime.review["status"] != "passed":
            raise DomainError("RULES_BLOCKED", "确认时审核未通过或工具不可用")
        runtime.validate_save(conn, current["payload"]["suggested_slot"])

    try:
        runtime.receipt = repo.confirm(draft_id, proposal_version, token, mode, decision, validate)
        runtime.proposal = runtime.receipt["payload"]
        runtime.review = runtime.proposal["review"]
        runtime.final_status = "completed" if decision == "approve" else "awaiting_user"
        trace.emit(
            "confirmation_result",
            decision=decision,
            draft_id=draft_id,
            draft_status=runtime.receipt["draft_status"],
            action_source="user_confirmation",
        )
    except DomainError as exc:
        runtime.last_error = failure(exc)
        runtime.final_status = (
            "awaiting_user" if exc.code in {"STALE_PROPOSAL", "SLOT_CONFLICT", "RULES_BLOCKED"} else "failed"
        )
        runtime.stop_reason = exc.code
        trace.emit("confirmation_rejected", code=exc.code, summary=exc.message)
        output["errors"].append(
            {
                "code": exc.code,
                "summary": exc.message,
                "next_action": "重新执行原 run 请求，生成新提案后再次确认",
            }
        )
    output["items"] = [runtime.result()]
    output["run_status"] = runtime.final_status
    return finish_run(output, trace, started, dataset, repo, count)
