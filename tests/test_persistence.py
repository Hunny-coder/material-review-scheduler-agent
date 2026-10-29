from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import ROOT, replace_json

from material_scheduler.repository import Repository
from material_scheduler.service import confirm_draft, run_request


def confirmation(project, item, directory="confirmation", **kwargs):
    return confirm_draft(
        draft_id=item["draft_save"]["draft_id"],
        proposal_version=item["proposal_version"],
        token=item["confirmation_token"],
        db=project["db"],
        data_dir=project["data"],
        run_dir=project["root"] / directory,
        **kwargs,
    )


@pytest.mark.parametrize("name", ["normal", "conflict"])
def test_duplicate_is_one_draft(run, project, name):
    first = run(name)["items"][0]
    second = run(name)["items"][0]
    assert first["draft_save"]["draft_id"] == second["draft_save"]["draft_id"]
    assert second["draft_save"]["status"] == "already_saved"
    assert Repository(project["db"]).count() == 1


@pytest.mark.parametrize("fault", ["before_save", "before_commit"])
def test_save_failure_is_atomic(run, project, fault):
    result = run("failure", fault={"save": fault})
    assert result["exit_code"] == 4
    item = result["items"][0]
    assert item["draft_save"]["status"] == "failed" and item["draft_save"]["draft_id"] is None
    repo = Repository(project["db"])
    assert repo.count() == 0
    with repo.connection() as conn:
        assert conn.execute("select count(*) from receipts").fetchone()[0] == 0
    retry = run("failure")
    assert retry["run_status"] == "completed" and repo.count() == 1


def test_post_commit_response_is_reconciled(run, project):
    item = run(fault={"save": "after_commit"})["items"][0]
    assert item["draft_save"]["status"] == "already_saved"
    assert item["draft_save"]["draft_id"] and Repository(project["db"]).count() == 1


def test_uncertain_receipt_is_not_reported_as_failure_or_success(run, project):
    result = run(fault={"save": "after_commit_unreadable"})
    assert result["run_status"] == "incomplete"
    assert result["items"][0]["draft_save"]["status"] == "unknown"
    assert Repository(project["db"]).count() == 1
    assert run()["items"][0]["draft_save"]["status"] == "already_saved"


def test_changed_risk_invalidates_previously_confirmed_draft(run, project):
    first = run()["items"][0]
    replace_json(
        project["data"] / "materials.json", lambda data: data["M01"].update(license_status="expired")
    )
    assert run()["items"][0]["review_status"] == "blocked"
    assert Repository(project["db"]).get(first["draft_save"]["draft_id"])["status"] == "invalidated"


def test_confirm_updates_same_draft_and_replay(run, project):
    first = run("conflict")["items"][0]
    result = confirmation(project, first)
    item = result["items"][0]
    assert item["confirmation_status"] == "confirmed" and item["draft_status"] == "confirmed"
    assert item["effective_slot"]["start"] == "2026-11-01T19:00:00+08:00"
    assert item["original_requirement"]["at"] == "2026-11-01T18:00:00+08:00"
    assert item["draft_save"]["draft_id"] == first["draft_save"]["draft_id"]
    again = confirmation(project, first, directory="confirm-again")
    assert again["items"][0]["draft_save"]["status"] == "already_saved"
    rerun = run("conflict")["items"][0]
    assert rerun["confirmation_status"] == "confirmed"
    assert Repository(project["db"]).count() == 1
    with Repository(project["db"]).connection() as conn:
        assert conn.execute("select count(*) from confirmation_events").fetchone()[0] == 1


def test_wrong_token_cannot_confirm(run, project):
    item = run("conflict")["items"][0]
    item["confirmation_token"] = "not-the-token"
    result = confirmation(project, item)
    assert result["errors"][0]["code"] == "INVALID_CONFIRMATION"
    assert Repository(project["db"]).get(item["draft_save"]["draft_id"])["status"] == "pending_confirmation"


def test_reject_does_not_reserve_slot(run, project):
    item = run("conflict")["items"][0]
    result = confirmation(project, item, decision="reject")
    assert result["items"][0]["confirmation_status"] == "rejected"
    assert result["items"][0]["effective_slot"] is None
    assert Repository(project["db"]).occupancy("demo_channel", "account-A") == []


@pytest.mark.parametrize(
    "change,expected",
    [
        ("slot", "SLOT_CONFLICT"),
        ("rules", "STALE_PROPOSAL"),
        ("material", "STALE_PROPOSAL"),
        ("risk", "RULES_BLOCKED"),
    ],
)
def test_stale_confirmation_invalidated(run, project, change, expected):
    item = run("conflict")["items"][0]
    if change == "slot":
        (project["data"] / "schedules.json").write_text(
            (ROOT / "examples/fixtures/stale/schedules.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
    elif change == "rules":
        replace_json(project["data"] / "rules.json", lambda data: data.update(version="v2"))
    else:
        replace_json(
            project["data"] / "materials.json",
            lambda data: data["M01"].update(
                {"title": "更新标题"} if change == "material" else {"license_status": "expired"}
            ),
        )
    result = confirmation(project, item)
    assert result["errors"][0]["code"] == expected
    assert result["items"][0]["effective_slot"] is None
    assert Repository(project["db"]).get(item["draft_save"]["draft_id"])["status"] == "invalidated"


def test_same_request_changed_input_rejected(run):
    import json

    raw = json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
    run(raw=raw)
    raw["items"][0]["publish_requirement"]["at"] = "2026-11-02T19:00:00+08:00"
    result = run(raw=raw)
    assert result["exit_code"] == 5 and result["errors"][0]["code"] == "IDEMPOTENCY_CONFLICT"


def test_concurrent_duplicate(project):
    import json

    raw = json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
    Repository(project["db"])

    def execute(index):
        return run_request(
            raw, db=project["db"], data_dir=project["data"], run_dir=project["root"] / f"concurrent-{index}"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [1, 2]))
    assert all(result["run_status"] == "completed" for result in results)
    assert len({result["items"][0]["draft_save"]["draft_id"] for result in results}) == 1
    assert Repository(project["db"]).count() == 1
