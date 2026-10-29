import json

import pytest
from conftest import ROOT, replace_json

from material_scheduler.repository import Repository


def actions(output):
    return [
        event["next_action"]
        for event in map(json.loads, open(output["trace_file"], encoding="utf-8"))
        if event["event"] == "model_action"
    ]


def test_normal(run, project):
    result = run()
    item = result["items"][0]
    assert result["run_status"] == "completed" and result["exit_code"] == 0
    assert result["publication_status"] == "not_published"
    assert item["review_status"] == "passed"
    assert item["effective_slot"]["start"] == "2026-11-02T18:00:00+08:00"
    assert item["draft_save"]["status"] == "saved"
    assert Repository(project["db"]).count() == 1


def test_missing_is_supplemented_then_stopped(run, project):
    result = run("missing")
    item = result["items"][0]
    assert item["review_status"] == "needs_info"
    assert item["missing_fields"] == ["license_id"]
    assert "read_supplement" in actions(result)
    assert "save_draft" not in actions(result)
    assert result["exit_code"] == 2 and Repository(project["db"]).count() == 0


def test_recoverable_supplement(run):
    result = run("recoverable")
    assert "read_supplement" in actions(result)
    assert result["run_status"] == "completed"
    assert result["items"][0]["review_status"] == "passed"


@pytest.mark.parametrize("name,status,code", [("risk", "blocked", 3), ("manual-review", "needs_review", 2)])
def test_rule_risks(run, project, name, status, code):
    result = run(name)
    item = result["items"][0]
    assert item["review_status"] == status and item["risk_reasons"]
    assert item["draft_save"]["status"] == "not_attempted"
    assert result["exit_code"] == code and Repository(project["db"]).count() == 0


def test_conflict_changes_path_and_preserves_hard_requirement(run):
    normal = run()
    conflict = run("conflict")
    item = conflict["items"][0]
    assert actions(normal).count("query_slots") == 1
    assert actions(conflict).count("query_slots") == 2
    assert item["original_requirement"]["at"] == "2026-11-01T18:00:00+08:00"
    assert item["suggested_slot"]["start"] == "2026-11-01T19:00:00+08:00"
    assert item["effective_slot"] is None and item["confirmation_status"] == "pending"
    assert item["draft_status"] == "pending_confirmation" and conflict["exit_code"] == 2


def test_changed_occupancy_updates_one_draft(run, project):
    first = run("conflict")["items"][0]
    project["data"].joinpath("schedules.json").write_text(
        (ROOT / "examples/fixtures/shifted/schedules.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    second = run("conflict")["items"][0]
    assert first["draft_save"]["draft_id"] == second["draft_save"]["draft_id"]
    assert second["proposal_version"] == 2
    assert second["effective_slot"]["start"] == "2026-11-01T18:00:00+08:00"
    assert second["confirmation_status"] == "not_required"
    assert Repository(project["db"]).count() == 1


def test_no_slot(run):
    result = run("no-slot", data_dir=ROOT / "examples/fixtures/no-slot")
    item = result["items"][0]
    assert item["schedule_status"] == "no_slot" and item["pending_items"]
    assert item["draft_save"]["status"] == "not_attempted"


def test_batch_sees_prior_confirmed_draft(run):
    result = run("batch")
    first, second = result["items"]
    assert first["draft_status"] == "confirmed"
    assert second["draft_status"] == "pending_confirmation"
    assert first["suggested_slot"] != second["suggested_slot"]


def test_warning_does_not_block(run, project):
    replace_json(project["data"] / "materials.json", lambda data: data["M01"].update(title="长" * 50))
    item = run()["items"][0]
    assert item["review_status"] == "passed"
    assert item["risk_reasons"][0]["severity"] == "warning"


def test_rules_unavailable_never_pass(run, project):
    (project["data"] / "rules.json").write_text("{broken", encoding="utf-8")
    item = run()["items"][0]
    assert item["review_status"] == "pending" and item["run_status"] == "incomplete"
    assert item["draft_save"]["status"] == "not_attempted"


def test_injection_is_only_data(run):
    result = run("injection")
    assert result["run_status"] == "completed"
    assert "publish" not in actions(result)
    assert result["publication_status"] == "not_published"
