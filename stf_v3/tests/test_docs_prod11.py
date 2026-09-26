"""PROD-11: the runbook and dev plan carry what operations needs.

Skipped when ``docs/`` is absent (portable copy).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib

import pytest

_DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"
_RUNBOOK = _DOCS / "v3_ops_runbook.md"
_PLAN = _DOCS / "v3_dev_plan.md"
_DESIGN = _DOCS / "v3_design_doc.md"

pytestmark = pytest.mark.skipif(not _RUNBOOK.is_file() or not _PLAN.is_file(), reason="docs/ not present")


def test_runbook_has_the_diagnosis_chapter() -> None:
    """Wait reasons, the controller rules, the pre-deploy guard, stuck
    sessions, rollback and the test workshop are all written down."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "## 6. 诊断任务与按需模型（PROD-11）" in text
    for needle in (
        "controller_unresponsive", "gpu_busy", "manual_converting", "model_cooldown", "model_starting",
        "diagnosis_interrupted", "model_unavailable", "不自动重跑",
        "-q gpu,llm --concurrency 2", "只停**控制器自己拉起**", "~/stf_v3_evals/.lock",
        "predeploy_check.sh", "ALLOW_INTERRUPT=1", "install.sh",
        "alembic downgrade b2c3d4e5f6a7", "visible_vehicles.py", "chmod 600",
    ):
        assert needle in text, needle


def test_dev_plan_and_design_doc_record_prod11() -> None:
    """Kickoff decisions, the two deferred FMs, and the design doc section."""
    plan = _PLAN.read_text(encoding="utf-8")
    for needle in ("D1 = A", "D4 = A", "测试车队刷诊断挤占真用户（PROD-11 FM-42）",
                   "V3 拖累共享 Postgres 连接数（PROD-11 FM-43）", "诊断任务共用锁 `diagnosis-model`"):
        assert needle in plan, needle
    design = _DESIGN.read_text(encoding="utf-8")
    assert "诊断任务与过程直播（PROD-11" in design and "model_service_state" in design and "图已同步" in design
