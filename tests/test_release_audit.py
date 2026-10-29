import json
from pathlib import Path

from conftest import ROOT, replace_json
from langchain_core.messages import AIMessage, ToolMessage

from material_scheduler.models import MockModel


def test_new_risk_cannot_leave_old_saved_receipt_effective(run, project):
    class ReviewAgain:
        changed = False

        def invoke(self, messages):
            saved = any(
                isinstance(message, ToolMessage) and message.name == "save_draft" for message in messages
            )
            if saved and not self.changed:
                self.changed = True
                replace_json(
                    project["data"] / "materials.json",
                    lambda data: data["M01"].update(license_status="expired"),
                )
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_material",
                            "id": "recheck-after-save",
                            "args": {
                                "material_id": "M01",
                                "decision_summary": "测试：保存后重新读取已变化的素材",
                            },
                        }
                    ],
                )
            return MockModel().invoke(messages)

    result = run(model_factory=lambda _: ReviewAgain())
    item = result["items"][0]
    assert item["review_status"] == "blocked" and result["run_status"] == "blocked"
    assert item["draft_status"] == "invalidated" and item["effective_slot"] is None
    assert item["draft_save"]["status"] == "saved"  # Historical transaction fact remains true.


def test_batch_pending_suggestions_avoid_each_other(run):
    raw = json.loads((ROOT / "examples/inputs/conflict.json").read_text(encoding="utf-8"))
    raw["items"].append({**raw["items"][0], "material_id": "M06"})
    result = run(raw=raw)
    first, second = result["items"]
    assert first["confirmation_status"] == second["confirmation_status"] == "pending"
    assert first["suggested_slot"] != second["suggested_slot"]


def test_unavoidable_pending_overlap_is_explicit(run):
    raw = json.loads((ROOT / "examples/inputs/conflict.json").read_text(encoding="utf-8"))
    raw["items"][0]["alternative_search_window"]["end"] = "2026-11-01T19:30:00+08:00"
    raw["items"].append({**raw["items"][0], "material_id": "M06"})
    first, second = run(raw=raw)["items"]
    assert first["mutually_exclusive_with"] == ["M06"]
    assert second["mutually_exclusive_with"] == ["M01"]
    assert first["effective_slot"] is second["effective_slot"] is None


def test_no_base_url_in_environment_files():
    for path in [ROOT / ".env.example", ROOT / ".env"]:
        if path.is_file():
            assert not any(
                line.lstrip().startswith("MODEL_BASE_URL=")
                for line in path.read_text(encoding="utf-8").splitlines()
            )


def test_mixed_malformed_tool_batch_is_rejected_as_a_unit(run):
    class MixedOnce:
        first = True

        def invoke(self, messages):
            if self.first:
                self.first = False
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "valid-read", "name": "read_material", "args": {"material_id": "M01"}}
                    ],
                    invalid_tool_calls=[
                        {
                            "id": "invalid-save",
                            "name": "save_draft",
                            "args": "{bad",
                            "error": "invalid JSON",
                            "type": "invalid_tool_call",
                        }
                    ],
                )
            return MockModel().invoke(messages)

    result = run(model_factory=lambda _: MixedOnce())
    events = [
        json.loads(line) for line in Path(result["trace_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert result["run_status"] == "completed"
    assert not any(
        event.get("tool_call_id") == "valid-read" and event["event"] == "tool_result" for event in events
    )


def test_public_trace_paths_do_not_expose_workspace():
    from material_scheduler.tracing import public_paths

    payload = {
        "path": str(Path.cwd() / "examples/trace.jsonl"),
        "original_requirement": {"at": "2026-11-01T18:00:00+08:00"},
    }
    assert public_paths(payload) == {
        "path": "examples/trace.jsonl",
        "original_requirement": payload["original_requirement"],
    }
