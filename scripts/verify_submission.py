"""Verify submission layout, evidence, links, ignored secrets and provider-only configuration."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from material_scheduler.policy import digest
from material_scheduler.tracing import now, write_json

ROOT = Path(__file__).resolve().parents[1]


def has_plain_token(value):
    if isinstance(value, dict):
        return any(
            (
                key in {"confirmation_token", "token", "api_key", "MODEL_API_KEY"}
                and bool(item)
                and item != "[redacted]"
            )
            or has_plain_token(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(has_plain_token(item) for item in value)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="examples/test-results/submission-audit.json")
    args = parser.parse_args()
    os.chdir(ROOT)
    checks = {}
    required = [
        "README.md",
        "pyproject.toml",
        "requirements.lock.txt",
        ".env.example",
        "项目开发文档.md",
        *[
            f"docs/0{number}-{name}.md"
            for number, name in enumerate(
                ["requirements", "solution", "evaluation", "ai-usage", "retrospective"], 1
            )
        ],
    ]
    checks["required_documents"] = all(Path(name).is_file() for name in required)
    checks["required_directories"] = all(Path(name).is_dir() for name in ["src", "tests", "examples", "data"])
    paths = [
        Path(name)
        for name in subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode()
        .split("\0")
        if name
    ]
    paths = [path for path in paths if path.is_file()]
    forbidden = {".venv", ".runtime", ".cache", "var", "__pycache__", ".pytest_cache", ".ruff_cache"}
    checks["no_runtime_files"] = not any(
        forbidden.intersection(path.parts) or path.name == ".env" or path.suffix in {".pyc", ".sqlite3"}
        for path in paths
    )
    secret = dotenv_values(".env").get("MODEL_API_KEY") if Path(".env").is_file() else None
    leaked = []
    for path in paths:
        content = path.read_bytes()
        if (secret and secret.encode() in content) or re.search(rb"sk-[A-Za-z0-9]{24,}", content):
            leaked.append(str(path))
    checks["no_api_key_in_submission"] = not leaked
    plain_token_files = []
    for path in Path("examples").rglob("*"):
        if (
            path.suffix == ".json"
            and has_plain_token(json.loads(path.read_text(encoding="utf-8")))
            or path.suffix == ".jsonl"
            and any(
                has_plain_token(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
            )
        ):
            plain_token_files.append(str(path))
    checks["no_plain_confirmation_tokens"] = not plain_token_files
    checks["no_base_url_in_env"] = all(
        not any(
            line.lstrip().startswith("MODEL_BASE_URL=")
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        for path in [Path(".env"), Path(".env.example")]
        if path.is_file()
    )
    checks["local_env_private"] = not Path(".env").exists() or (os.stat(".env").st_mode & 0o777) == 0o600
    broken = []
    for path in [
        Path("README.md"),
        Path("项目开发文档.md"),
        Path("examples/README.md"),
        *Path("docs").glob("*.md"),
    ]:
        text = path.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if (
                "://" not in link
                and not link.startswith("#")
                and (path.parent / link.split("#")[0]).resolve() != Path(args.report).resolve()
                and not (path.parent / link.split("#")[0]).exists()
            ):
                broken.append({"document": str(path), "target": link})
    checks["documentation_links"] = not broken
    source_hash = digest(
        {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(Path("src/material_scheduler").glob("*.py"))
        }
    )
    evidence = Path("examples/runs/release/acceptance/summary.json")
    checks["six_live_acceptance_cases"] = (
        evidence.is_file() and json.loads(evidence.read_text(encoding="utf-8")).get("passed") == 6
    )
    directories = list(Path("examples/runs/release").glob("*/*/run-metadata.json"))
    checks["current_release_source_hash"] = bool(directories) and all(
        json.loads(path.read_text(encoding="utf-8"))["source_hash"] == source_hash for path in directories
    )
    manifest = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if str(path) != args.report
    }
    report = {
        "checked_at": now(),
        "passed": all(checks.values()),
        "checks": checks,
        "leaked_files": leaked,
        "broken_links": broken,
        "source_hash": source_hash,
        "submission_files": len(paths),
        "content_manifest": manifest,
        "repository_url": "https://github.com/Hunny-coder/material-review-scheduler-agent",
        "question_id": None,
        "question_id_note": "暂无，用户已确认",
    }
    write_json(args.report, report)
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
