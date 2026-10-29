import json
import shutil
from pathlib import Path

import pytest

from material_scheduler.service import run_request

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    return {"root": tmp_path, "data": data, "db": tmp_path / "test.sqlite3", "sequence": 0}


@pytest.fixture
def run(project):
    def execute(name="normal", raw=None, **kwargs):
        project["sequence"] += 1
        if raw is None:
            raw = json.loads((ROOT / "examples" / "inputs" / f"{name}.json").read_text(encoding="utf-8"))
        options = {
            "db": project["db"],
            "data_dir": project["data"],
            "run_dir": project["root"] / f"run-{project['sequence']}",
        }
        return run_request(raw, **{**options, **kwargs})

    return execute


def replace_json(path, transform):
    payload = json.loads(path.read_text(encoding="utf-8"))
    transform(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
