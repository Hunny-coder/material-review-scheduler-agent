import json
from pathlib import Path

import pytest
from conftest import ROOT
from langchain_core.messages import AIMessage

from material_scheduler.models import MockModel
from material_scheduler.policy import overlaps
from material_scheduler.repository import Repository
from material_scheduler.schemas import MaterialItem


@pytest.mark.parametrize(
    "limits,reason,calls",
    [({"max_tool_calls": 2}, "MAX_TOOL_CALLS", 2), ({"max_model_calls": 2}, "MAX_MODEL_CALLS", 2)],
)
def test_limits_are_explicit(run, limits, reason, calls):
    result = run("budget", fault={"model": "loop"}, **limits)
    item = result["items"][0]
    assert result["exit_code"] == 6 and item["run_status"] == "incomplete"
    assert item["stop_reason"] == reason and item["tool_calls"] == calls
    assert item["draft_save"]["status"] == "not_attempted"


def test_budget_after_save_preserves_true_receipt(run, project):
    result = run(max_model_calls=5)
    item = result["items"][0]
    assert item["run_status"] == "incomplete" and item["stop_reason"] == "MAX_MODEL_CALLS"
    assert item["draft_save"]["status"] == "saved" and Repository(project["db"]).count() == 1


def test_malicious_save_parameters_rejected(run, project):
    result = run("boundary", fault={"model": "boundary"})
    assert Repository(project["db"]).count() == 0
    trace = [json.loads(line) for line in Path(result["trace_file"]).read_text(encoding="utf-8").splitlines()]
    assert any(event.get("result", {}).get("code") == "INVALID_ACTION" for event in trace)


class ScriptModel:
    def __init__(self, tool, args):
        self.tool, self.args, self.called = tool, args, False

    def invoke(self, _messages):
        if self.called:
            return AIMessage(content="模型声称成功也不能改变业务事实")
        self.called = True
        return AIMessage(content="", tool_calls=[{"name": self.tool, "args": self.args, "id": "test-1"}])


@pytest.mark.parametrize(
    "tool,args,code",
    [
        ("publish", {}, "INVALID_ACTION"),
        ("save_draft", {"material_id": "M01", "proposal_version": 1}, "STALE_PROPOSAL"),
        (
            "query_slots",
            {
                "channel": "demo_channel",
                "account_id": "account-A",
                "start": "2026-11-03T18:00:00+08:00",
                "end": "2026-11-03T18:30:00+08:00",
            },
            "OUTSIDE_AUTHORIZED_WINDOW",
        ),
    ],
)
def test_invalid_actions_cannot_claim_success(run, project, tool, args, code):
    result = run(model_factory=lambda _: ScriptModel(tool, args))
    assert Repository(project["db"]).count() == 0
    assert result["items"][0]["stop_reason"] == code
    assert result["items"][0]["draft_save"]["status"] == "not_attempted"


def test_unknown_material_not_fabricated(run):
    raw = json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
    raw["items"][0]["material_id"] = "DOES-NOT-EXIST"
    item = run(raw=raw)["items"][0]
    assert item["review_status"] != "passed" and item["stop_reason"] == "MATERIAL_NOT_FOUND"


def test_live_unavailable_does_not_fallback(run, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    result = run(mode="live")
    assert result["mode"] == "live" and result["exit_code"] == 7
    assert result["items"][0]["stop_reason"] == "MODEL_UNAVAILABLE"
    assert result["items"][0]["tool_calls"] == 0


def test_invalid_sibling_does_not_block_valid_item(run):
    raw = json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
    raw["items"].append({"material_id": "bad"})
    result = run(raw=raw)
    assert result["items"][0]["run_status"] == "completed"
    assert result["items"][1]["stop_reason"] == "INVALID_INPUT" and result["exit_code"] == 5


def test_duplicate_item_normalized(run):
    raw = json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
    raw["items"].append(raw["items"][0].copy())
    result = run(raw=raw)
    assert len(result["items"]) == 1 and result["notices"]


@pytest.mark.parametrize(
    "kind,at,expected",
    [
        ("window", None, "2026-11-01T19:00:00+08:00"),
        ("preferred", "2026-11-01T20:00:00+08:00", "2026-11-01T20:00:00+08:00"),
    ],
)
def test_explicitly_authorized_window(run, kind, at, expected):
    raw = json.loads((ROOT / "examples/inputs/conflict.json").read_text(encoding="utf-8"))
    requirement = {"kind": kind, "start": "2026-11-01T18:00:00+08:00", "end": "2026-11-01T21:00:00+08:00"}
    if at:
        requirement["at"] = at
    raw["items"][0]["publish_requirement"] = requirement
    item = run(raw=raw)["items"][0]
    assert item["effective_slot"]["start"] == expected and item["confirmation_status"] == "not_required"


def test_half_open_intervals_and_utc():
    left = {"start": "2026-11-01T18:00:00+08:00", "end": "2026-11-01T18:30:00+08:00"}
    right = {"start": "2026-11-01T18:30:00+08:00", "end": "2026-11-01T19:00:00+08:00"}
    same = {"start": "2026-11-01T10:00:00+00:00", "end": "2026-11-01T10:30:00+00:00"}
    assert not overlaps(left, right)
    assert overlaps(left, same)


@pytest.mark.parametrize("at", ["2026-11-01T18:00:00", "2026-11-01T18:15:00+08:00"])
def test_ambiguous_or_unaligned_time_rejected(at):
    with pytest.raises(ValueError):
        MaterialItem.model_validate(
            {
                "material_id": "M01",
                "channel": "demo_channel",
                "account_id": "account-A",
                "publish_requirement": {"at": at},
            }
        )


def test_trace_redacts_confirmation_token(run):
    result = run("conflict")
    token = result["items"][0]["confirmation_token"]
    assert token not in Path(result["trace_file"]).read_text(encoding="utf-8")


def test_run_directory_cannot_overwrite_evidence(run, project):
    directory = project["root"] / "immutable-run"
    run(run_dir=directory)
    with pytest.raises(ValueError, match="已有 Trace"):
        run(run_dir=directory)


@pytest.mark.parametrize("value", [[], None, {"rules": [{"operator": "unknown"}]}])
def test_invalid_rule_structure_never_passes(run, project, value):
    (project["data"] / "rules.json").write_text(json.dumps(value), encoding="utf-8")
    item = run()["items"][0]
    assert item["review_status"] != "passed" and item["draft_save"]["status"] == "not_attempted"


def test_non_object_request_is_rejected(run):
    result = run(raw=[])
    assert result["exit_code"] == 5 and result["errors"][0]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("invalid_args", [True, False])
def test_malformed_model_tool_action_gets_observation_not_success(run, invalid_args):
    class MalformedOnce:
        def __init__(self):
            self.first = True

        def invoke(self, messages):
            if self.first:
                self.first = False
                return AIMessage(
                    content="",
                    response_metadata={"finish_reason": "tool_calls"},
                    invalid_tool_calls=[
                        {
                            "name": "save_draft",
                            "args": "{broken",
                            "id": "bad-1",
                            "error": "invalid JSON",
                            "type": "invalid_tool_call",
                        }
                    ]
                    if invalid_args
                    else [],
                )
            return MockModel().invoke(messages)

    result = run(model_factory=lambda _: MalformedOnce())
    assert result["run_status"] == "completed"
    assert result["items"][0]["tool_calls"] == 7
    trace = [json.loads(line) for line in Path(result["trace_file"]).read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "model_invalid_action" for event in trace)
