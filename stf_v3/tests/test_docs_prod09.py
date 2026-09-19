"""PROD-09 T-16: the runbook and dev plan carry what operations needs.

Skipped when ``docs/`` is absent (portable copy).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib

import pytest

_DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"
_RUNBOOK = _DOCS / "v3_ops_runbook.md"
_PLAN = _DOCS / "v3_dev_plan.md"

pytestmark = pytest.mark.skipif(not _RUNBOOK.is_file() or not _PLAN.is_file(), reason="docs/ not present")


def test_runbook_has_the_model_service_chapter() -> None:
    """FM-9 / FM-10 / FM-13 / FM-21: startup order, fallback steps, the
    recipe-vs-bake-off table, the fields allowed to leave for cloud runs,
    and the troubleshooting entries."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "## 4. 模型服务（vLLM）" in text
    for needle in (
        "vllm_ctl.sh start", "vllm_ctl.sh wait", "vllm_ctl.sh stop",
        "启动顺序", "回退到 Ollama", "切回 vLLM",
        "bake-off", "0.80", "HF_HUB_OFFLINE",
        "允许出境的字段", "--cloud",
        "LLM_CHECK=skip", "nvidia-smi",
    ):
        assert needle in text, needle


def test_dev_plan_records_the_deferred_items() -> None:
    """The five deferred items have their return conditions in §4."""
    text = _PLAN.read_text(encoding="utf-8")
    for needle in (
        "预算校准（PROD-09 FM-25）",
        "V1/V2 与 vLLM 抢显存（PROD-09 FM-27）",
        "开/关思考的影响（PROD-09 FM-45）",
        "嵌入模型（PROD-09 D4）",
        "手册图片（PROD-09 D5）",
    ):
        assert needle in text, needle
