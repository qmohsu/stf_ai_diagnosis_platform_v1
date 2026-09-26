"""User-facing codes and sentences for waits and failures (PROD-11).

Events and API bodies carry a stable ``code`` plus a short sentence in
the deployment's report language; raw exception text never leaves the
server log (FM-38).  ``WAIT_REASONS`` are the ``waiting`` event reasons
(D2), ``ERROR_CODES`` the codes a conversation can end with.

Author: Xiangzhu Yan
"""

from typing import Dict

WAIT_REASONS: Dict[str, Dict[str, str]] = {
    "queued": {
        "zh-TW": "排隊中，前面還有 {ahead} 個診斷",
        "zh-CN": "排队中，前面还有 {ahead} 个诊断",
        "en": "Queued, {ahead} diagnosis run(s) ahead",
    },
    "model_starting": {
        "zh-TW": "模型啟動中，約 {eta_min} 分鐘",
        "zh-CN": "模型启动中，约 {eta_min} 分钟",
        "en": "Starting the model, about {eta_min} min",
    },
    "gpu_busy": {
        "zh-TW": "顯示卡正被其他團隊使用，排隊等待中",
        "zh-CN": "显卡正被其他团队使用，排队等待中",
        "en": "The GPUs are in use by another team; waiting",
    },
    "manual_converting": {
        "zh-TW": "顯示卡正在轉換手冊，排隊等待中",
        "zh-CN": "显卡正在转换手册，排队等待中",
        "en": "A manual is being converted on the GPU; waiting",
    },
    "controller_unresponsive": {
        "zh-TW": "模型控制程式暫無回應，等待恢復",
        "zh-CN": "模型控制程序暂无响应，等待恢复",
        "en": "The model controller is not responding; waiting",
    },
    "model_cooldown": {
        "zh-TW": "模型剛才啟動失敗，稍後自動重試",
        "zh-CN": "模型刚才启动失败，稍后自动重试",
        "en": "The model failed to start; retrying shortly",
    },
}

ERROR_CODES: Dict[str, Dict[str, str]] = {
    "model_unavailable": {
        "zh-TW": "模型暫不可用，請稍後重新診斷",
        "zh-CN": "模型暂不可用，请稍后重新诊断",
        "en": "The model is not available; please try again later",
    },
    "model_start_failed": {
        "zh-TW": "模型啟動失敗，請稍後重新診斷",
        "zh-CN": "模型启动失败，请稍后重新诊断",
        "en": "The model failed to start; please try again later",
    },
    "diagnosis_interrupted": {
        "zh-TW": "診斷被中斷，請重新發起",
        "zh-CN": "诊断被中断，请重新发起",
        "en": "The diagnosis was interrupted; please start it again",
    },
    "vehicle_deleted": {
        "zh-TW": "車輛已刪除",
        "zh-CN": "车辆已删除",
        "en": "The vehicle was deleted",
    },
    "log_unavailable": {
        "zh-TW": "日誌已刪除或無法讀取",
        "zh-CN": "日志已删除或无法读取",
        "en": "The log was deleted or cannot be read",
    },
    "queue_unavailable": {
        "zh-TW": "無法排入診斷佇列，請稍後重試",
        "zh-CN": "无法排入诊断队列，请稍后重试",
        "en": "Could not queue the diagnosis; please try again",
    },
    "run_failed": {
        "zh-TW": "診斷未產出任何內容，請重新發起",
        "zh-CN": "诊断未产出任何内容，请重新发起",
        "en": "The diagnosis produced no output; please start it again",
    },
    "internal_error": {
        "zh-TW": "診斷時發生內部錯誤，請重新發起",
        "zh-CN": "诊断时发生内部错误，请重新发起",
        "en": "Internal error during the diagnosis; please start it again",
    },
}


# Why an engine run stopped early (the engine's ``error`` event); the
# report is then partial (or empty).  Maps the engine's stopped_reason.
STOP_REASONS: Dict[str, Dict[str, str]] = {
    "timeout": {
        "zh-TW": "已達診斷時間上限，以下為部分報告",
        "zh-CN": "已达诊断时间上限，以下为部分报告",
        "en": "The diagnosis hit its time limit; the report is partial",
    },
    "budget": {
        "zh-TW": "已達診斷用量上限，以下為部分報告",
        "zh-CN": "已达诊断用量上限，以下为部分报告",
        "en": "The diagnosis hit its usage budget; the report is partial",
    },
    "cancelled": {
        "zh-TW": "診斷已取消",
        "zh-CN": "诊断已取消",
        "en": "The diagnosis was cancelled",
    },
    "error": {
        "zh-TW": "模型或執行時發生錯誤，以下為部分報告",
        "zh-CN": "模型或运行时发生错误，以下为部分报告",
        "en": "A model or runtime error stopped the diagnosis; the report is partial",
    },
}


def _pick(table: Dict[str, str], locale: str) -> str:
    return table.get(locale) or table.get("zh-TW" if locale.startswith("zh") else "en") or table["en"]


def wait_text(reason: str, locale: str, **fmt: object) -> str:
    """Sentence for a ``waiting`` reason (unknown reasons fall back to the code)."""
    table = WAIT_REASONS.get(reason)
    if table is None:
        return reason
    try:
        return _pick(table, locale).format(**fmt)
    except (KeyError, IndexError):
        return _pick(table, locale)


def error_text(code: str, locale: str) -> str:
    """Sentence for an error code (``internal_error`` for unknown codes)."""
    return _pick(ERROR_CODES.get(code) or ERROR_CODES["internal_error"], locale)


def stop_text(stopped_reason: str, locale: str) -> str:
    """Sentence for an early stop of the engine (unknown → the ``error`` one)."""
    return _pick(STOP_REASONS.get(stopped_reason) or STOP_REASONS["error"], locale)
