"""Execute examples and preserve real outputs, observations and assertions (Mock != real LLM)."""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from material_scheduler.repository import Repository
from material_scheduler.service import confirm_draft, run_request
from material_scheduler.tracing import write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--output", type=Path, default=ROOT / "examples/runs")
    args = parser.parse_args()
    # Same local .env semantics as CLI, without searching ancestor directories.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    work = ROOT / "var" / "example-work" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True)
    summaries = []

    def execute(scenario, *, label=None, db_name=None, data_dir=None, **options):
        label = label or scenario
        raw = json.loads((ROOT / "examples/inputs" / f"{scenario}.json").read_text(encoding="utf-8"))
        result = run_request(
            raw,
            mode=args.mode,
            db=work / f"{db_name or label}.sqlite3",
            data_dir=data_dir or ROOT / "data",
            run_dir=args.output / f"{args.mode}-{label}",
            **options,
        )
        summaries.append(
            {
                "scenario": label,
                "mode": args.mode,
                "run_status": result["run_status"],
                "exit_code": result["exit_code"],
                "run_directory": str(Path(result["trace_file"]).parent),
            }
        )
        return result

    def verify(output, **checks):
        directory = Path(output["trace_file"]).parent
        existing = json.loads((directory / "assertions.json").read_text(encoding="utf-8"))
        existing["scenario_checks"] = checks
        existing["scenario_passed"] = all(checks.values())
        write_json(directory / "assertions.json", existing)
        if not all(checks.values()):
            raise AssertionError(f"场景断言失败：{directory}：{checks}")

    normal = execute("normal")
    verify(
        normal,
        completed=normal["run_status"] == "completed",
        saved=normal["items"][0]["draft_save"]["status"] == "saved",
    )
    conflict = execute("conflict")
    item = conflict["items"][0]
    verify(
        conflict,
        pending=item["confirmation_status"] == "pending",
        ineffective=item["effective_slot"] is None,
        alternative_19=item["suggested_slot"]["start"] == "2026-11-01T19:00:00+08:00",
    )
    if args.mode == "live":
        write_json(args.output / "live-index.json", summaries)
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0
    duplicate = execute("conflict", label="duplicate", db_name="conflict")
    verify(
        duplicate,
        same_id=duplicate["items"][0]["draft_save"]["draft_id"] == item["draft_save"]["draft_id"],
        replayed=duplicate["items"][0]["draft_save"]["status"] == "already_saved",
        exactly_one=Repository(work / "conflict.sqlite3").count() == 1,
    )
    confirmed = confirm_draft(
        draft_id=item["draft_save"]["draft_id"],
        proposal_version=item["proposal_version"],
        token=item["confirmation_token"],
        db=work / "conflict.sqlite3",
        data_dir=ROOT / "data",
        run_dir=args.output / "mock-confirm",
        mode="mock",
    )
    summaries.append(
        {
            "scenario": "confirm",
            "mode": "mock",
            "run_status": confirmed["run_status"],
            "exit_code": confirmed["exit_code"],
            "run_directory": str(Path(confirmed["trace_file"]).parent),
        }
    )
    verify(
        confirmed,
        same_id=confirmed["items"][0]["draft_save"]["draft_id"] == item["draft_save"]["draft_id"],
        confirmed=confirmed["items"][0]["confirmation_status"] == "confirmed",
    )
    for scenario, expected in [
        ("missing", "needs_info"),
        ("risk", "blocked"),
        ("recoverable", "passed"),
        ("manual-review", "needs_review"),
    ]:
        result = execute(scenario)
        verify(result, review_matches=result["items"][0]["review_status"] == expected)
    failure = execute("failure", fault={"save": "before_commit"})
    verify(
        failure,
        failed=failure["items"][0]["draft_save"]["status"] == "failed",
        rolled_back=Repository(work / "failure.sqlite3").count() == 0,
    )
    budget = execute("budget", fault={"model": "loop"}, max_tool_calls=2)
    verify(
        budget, incomplete=budget["run_status"] == "incomplete", bounded=budget["items"][0]["tool_calls"] == 2
    )
    boundary = execute("boundary", fault={"model": "boundary"})
    verify(
        boundary,
        no_draft=Repository(work / "boundary.sqlite3").count() == 0,
        rejected=boundary["items"][0]["stop_reason"] == "INVALID_ACTION",
    )
    no_slot = execute("no-slot", data_dir=ROOT / "examples/fixtures/no-slot")
    verify(no_slot, no_slot=no_slot["items"][0]["schedule_status"] == "no_slot")
    batch = execute("batch")
    verify(
        batch, distinct_candidates=batch["items"][0]["suggested_slot"] != batch["items"][1]["suggested_slot"]
    )
    injection = execute("injection")
    verify(injection, not_published=injection["publication_status"] == "not_published")
    # Initial plan and changed occupancy use the SAME database and user request.
    mutable = work / "mutable-data"
    shutil.copytree(ROOT / "data", mutable)
    before = execute("conflict", label="before-shift", db_name="shift", data_dir=mutable)
    shutil.copyfile(ROOT / "examples/fixtures/shifted/schedules.json", mutable / "schedules.json")
    shifted = execute("conflict", label="shifted-conflict", db_name="shift", data_dir=mutable)
    verify(
        shifted,
        follows_new_data=shifted["items"][0]["effective_slot"]["start"] == "2026-11-01T18:00:00+08:00",
        same_id=shifted["items"][0]["draft_save"]["draft_id"] == before["items"][0]["draft_save"]["draft_id"],
        exactly_one=Repository(work / "shift.sqlite3").count() == 1,
    )
    stale_data = work / "stale-data"
    shutil.copytree(ROOT / "data", stale_data)
    stale_plan = execute("conflict", label="before-stale", db_name="stale", data_dir=stale_data)["items"][0]
    shutil.copyfile(ROOT / "examples/fixtures/stale/schedules.json", stale_data / "schedules.json")
    stale = confirm_draft(
        draft_id=stale_plan["draft_save"]["draft_id"],
        proposal_version=stale_plan["proposal_version"],
        token=stale_plan["confirmation_token"],
        db=work / "stale.sqlite3",
        data_dir=stale_data,
        run_dir=args.output / "mock-stale-confirm",
        mode="mock",
    )
    summaries.append(
        {
            "scenario": "stale-confirm",
            "mode": "mock",
            "run_status": stale["run_status"],
            "exit_code": stale["exit_code"],
            "run_directory": str(Path(stale["trace_file"]).parent),
        }
    )
    verify(
        stale,
        rejected=stale["errors"][0]["code"] == "SLOT_CONFLICT",
        ineffective=stale["items"][0]["effective_slot"] is None,
    )
    write_json(args.output / "mock-index.json", summaries)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
