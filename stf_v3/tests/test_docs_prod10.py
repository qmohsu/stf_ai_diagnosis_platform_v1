"""PROD-10 T-13: the runbook and dev plan carry what operations needs.

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


def test_runbook_has_the_golden_eval_chapter() -> None:
    """How to run, how long / how much, what to check when scores drop,
    what not to do meanwhile, the baseline reset and who sets the labels."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "## 5. Golden 评测（PROD-10）" in text
    for needle in (
        "run_golden_eval.sh --purpose gate", "tail -f", "exit_code",
        "多久、多少钱", "分数掉了先看哪", "regrade",
        "评测期间不要做", "不启动 V1/V2", "不上传手册",
        "基线重置", "baseline-reset", "eval-exempt", "**用户**",
        "精简版", "--thinking on --budget-scale 2", "--cloud",
        "按需启动", "vllm_ctl.sh stop", "LLM_CHECK=skip", "OBD 0.06", "0.884",
    ):
        assert needle in text, needle


def test_dev_plan_closes_and_adds_the_return_conditions() -> None:
    """§4: manual-id mapping closed; the two deferred PROD-10 items added;
    the thinking / budget / images rows still name PROD-10."""
    text = _PLAN.read_text(encoding="utf-8")
    assert "已处理（2026-09-23，PROD-10）" in text
    assert "已处理（2026-09-24：两次 golden 校准" in text          # FM-25 closed
    assert "部分处理（小助手结案，主 Agent 暂缓）" in text          # FM-45
    assert "依赖图六题 0.921" in text                                 # D5 images: numbers filled
    for needle in (
        "OBD 拒答方式适配（PROD-10 D5）",
        "vLLM 常驻与共享服务器（PROD-10）",
        "OBD 请求上限尾部（PROD-10）",
        "云端对照 OBD 工具参数（PROD-10）",
        "评测进程持有生产凭据（PROD-10 FM-16）",
        "评测与手册转换抢第二张卡（PROD-10 FM-38）",
        "预算校准（PROD-09 FM-25）",
        "开/关思考的影响（PROD-09 FM-45）",
        "手册图片（PROD-09 D5）",
    ):
        assert needle in text, needle
