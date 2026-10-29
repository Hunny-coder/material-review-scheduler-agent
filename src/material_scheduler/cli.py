"""CLI emits JSON and useful exit codes; it never calls a publishing API."""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .service import confirm_draft, run_request
from .tracing import versions


def main(argv=None):
    # Never search ancestor directories: a projectless parent may contain an unrelated .env/FIFO.
    environment_file = Path.cwd() / ".env"
    if environment_file.is_file():
        load_dotenv(environment_file)
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    parser = argparse.ArgumentParser(description="素材审核与发布排期 Agent（仅保存草稿）")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="审核并保存排期草稿")
    run.add_argument("--input", required=True)
    run.add_argument("--fault-config", help="仅测试用：显式失败注入配置")
    run.add_argument("--max-model-calls", type=int, default=int(os.getenv("AGENT_MAX_MODEL_CALLS", "12")))
    run.add_argument("--max-tool-calls", type=int, default=int(os.getenv("AGENT_MAX_TOOL_CALLS", "12")))
    confirm = commands.add_parser("confirm", help="确认或拒绝待确认草稿；重新校验资料和占用")
    confirm.add_argument("--draft-id", required=True)
    confirm.add_argument("--proposal-version", type=int, required=True)
    confirm.add_argument("--token", required=True)
    confirm.add_argument("--decision", choices=["approve", "reject"], default="approve")
    for command in [run, confirm]:
        command.add_argument("--mode", choices=["mock", "live"], default=os.getenv("AGENT_MODE", "mock"))
        command.add_argument("--db", default="var/review.sqlite3")
        command.add_argument("--data-dir", default="data")
        command.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    if args.version:
        print(json.dumps({"project": __version__, **versions()}, ensure_ascii=False, indent=2))
        return 0
    if not args.command:
        parser.print_help()
        return 0
    directory = args.run_dir or "var/runs/" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    options = {"mode": args.mode, "db": args.db, "data_dir": args.data_dir, "run_dir": directory}
    try:
        if args.command == "run":
            raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
            fault = (
                json.loads(Path(args.fault_config).read_text(encoding="utf-8")) if args.fault_config else None
            )
            if fault and args.mode != "mock":
                raise ValueError("失败注入仅允许在 mock 模式使用")
            result = run_request(
                raw,
                max_model_calls=args.max_model_calls,
                max_tool_calls=args.max_tool_calls,
                fault=fault,
                **options,
            )
        else:
            result = confirm_draft(
                draft_id=args.draft_id,
                proposal_version=args.proposal_version,
                token=args.token,
                decision=args.decision,
                **options,
            )
    except (OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "run_status": "failed",
                    "publication_status": "not_published",
                    "errors": [{"code": "INVALID_INPUT_OR_IO", "summary": str(exc)[:800]}],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 5
    except sqlite3.Error:
        print(
            json.dumps(
                {
                    "run_status": "failed",
                    "publication_status": "not_published",
                    "errors": [
                        {"code": "DATABASE_UNAVAILABLE", "summary": "数据库无法访问，未取得保存成功依据"}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 4
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
