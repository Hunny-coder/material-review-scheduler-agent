"""Explicit real-model checks; skipped evidence must never be labelled as passing live decisions."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@pytest.mark.live
@pytest.mark.parametrize("scenario,expected", [("normal", "completed"), ("conflict", "awaiting_user")])
def test_real_model_paths(run, scenario, expected):
    if not os.getenv("MODEL_API_KEY") or not os.getenv("MODEL_NAME"):
        pytest.skip("未配置真实模型凭据；Mock 不证明真实模型决策")
    result = run(scenario, mode="live")
    assert result["run_status"] == expected
    assert result["items"][0]["draft_save"]["status"] == "saved"
    assert result["items"][0]["tool_calls"] >= 5
