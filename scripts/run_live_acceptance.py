"""Run six acceptance cases with a real model and synthetic, local business tools."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from material_scheduler.models import model_endpoint
from material_scheduler.repository import Repository
from material_scheduler.service import run_request
from material_scheduler.tracing import write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; existing evidence is preserved"
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if not os.getenv("MODEL_NAME") or not os.getenv("MODEL_API_KEY"):
        parser.error("Configure MODEL_NAME and MODEL_API_KEY in local .env first")
    work = ROOT / "var/live-acceptance" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True)
    entries = []
    first_conflict = None
    for name in ["normal", "missing", "risk", "conflict", "duplicate", "failure"]:
        input_name = "conflict" if name == "duplicate" else name
        request = json.loads((ROOT / "examples/inputs" / f"{input_name}.json").read_text(encoding="utf-8"))
        db = work / f"{'conflict' if name == 'duplicate' else name}.sqlite3"
        # Only the local save tool is fault-injected. The model remains real, with mode=live.
        fault = {"save": "before_commit"} if name == "failure" else None
        result = run_request(
            request, mode="live", db=db, data_dir=ROOT / "data", run_dir=args.output / name, fault=fault
        )
        item = result["items"][0]
        checks = {"not_published": result["publication_status"] == "not_published"}
        if name == "normal":
            checks.update(
                completed=result["run_status"] == "completed", saved=item["draft_save"]["status"] == "saved"
            )
        elif name == "missing":
            checks.update(
                needs_info=item["review_status"] == "needs_info", no_draft=Repository(db).count() == 0
            )
        elif name == "risk":
            checks.update(blocked=item["review_status"] == "blocked", no_draft=Repository(db).count() == 0)
        elif name == "conflict":
            first_conflict = item
            checks.update(
                pending=item["confirmation_status"] == "pending",
                ineffective=item["effective_slot"] is None,
                original_preserved=item["original_requirement"]["at"] == "2026-11-01T18:00:00+08:00",
                saved=item["draft_save"]["status"] == "saved",
            )
        elif name == "duplicate":
            checks.update(
                replayed=item["draft_save"]["status"] == "already_saved",
                exactly_one=Repository(db).count() == 1,
                same_id=bool(
                    first_conflict
                    and item["draft_save"]["draft_id"] == first_conflict["draft_save"]["draft_id"]
                ),
            )
        else:
            checks.update(
                explicit_failure=item["draft_save"]["status"] == "failed",
                rollback=Repository(db).count() == 0,
                exit_code=result["exit_code"] == 4,
            )
        passed = all(checks.values())
        write_json(
            args.output / name / "acceptance.json",
            {"passed": passed, "checks": checks, "local_tool_fault": fault},
        )
        entries.append(
            {
                "scenario": name,
                "passed": passed,
                "checks": checks,
                "run_status": result["run_status"],
                "exit_code": result["exit_code"],
                "output": str(args.output / name / "output.json"),
            }
        )
        print(f"{name}: {'PASS' if passed else 'FAIL'} ({result['run_status']})", flush=True)
    summary = {
        "mode": "live",
        "model": os.environ["MODEL_NAME"],
        "provider": os.getenv("MODEL_PROVIDER", "openai"),
        "endpoint": model_endpoint(os.getenv("MODEL_PROVIDER", "openai")),
        "enable_thinking": os.getenv("MODEL_ENABLE_THINKING"),
        "total": len(entries),
        "passed": sum(entry["passed"] for entry in entries),
        "cases": entries,
    }
    write_json(args.output / "summary.json", summary)
    return 0 if all(entry["passed"] for entry in entries) else 1


if __name__ == "__main__":
    sys.exit(main())
