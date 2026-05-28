# -*- coding: utf-8 -*-
"""
后台定时任务（APScheduler）
──────────────────────────
只调度一个 cron 任务：完整数据流水线 `matchpredict.pipeline.run_pipeline(mode="full")`。
覆盖了 5 个步骤（赛程 → 赔率 → ML → 泊松比分 → 结果回填），跑完一轮就是一个完整周期，
无需再拆出"只回填结果"这种半截任务（要单独跑请用 CLI: `python -m matchpredict.pipeline.runner`）。
"""
import atexit
import logging
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from matchpredict.pipeline import run_pipeline

logger = logging.getLogger("matchpredict.scheduler")

# 流水线参数（如需调节，统一改这里）
_PIPELINE_DAYS_AHEAD = 7
_PIPELINE_DAYS_BACK = 7
# cron: 每小时第 5 分钟（避开整点 API 高峰，错开监控统计）
_PIPELINE_CRON = CronTrigger(minute=5)


def _run_pipeline_safe(include_cancelled: bool = False) -> dict:
    """同步调用流水线并归一化日志输出。任何异常都不应抛出到 APScheduler。"""
    try:
        result = run_pipeline(
            mode="full",
            days_ahead=_PIPELINE_DAYS_AHEAD,
            days_back=_PIPELINE_DAYS_BACK,
            include_cancelled=include_cancelled,
        )
        meta = result.get("_meta", {})
        errors = meta.get("total_errors", 0)
        processed = meta.get("total_processed", 0)
        elapsed = meta.get("elapsed_s", 0)
        if errors:
            logger.warning("⚠️ 流水线完成，错误=%d / 处理=%d / 耗时=%.1fs",
                           errors, processed, elapsed)
        else:
            logger.info("✅ 流水线完成，处理=%d / 耗时=%.1fs", processed, elapsed)
        return result
    except Exception as e:
        logger.error("❌ 流水线异常: %s", e, exc_info=True)
        return {"_meta": {"total_errors": 1, "total_processed": 0, "elapsed_s": 0,
                          "exception": str(e)}}


def setup_scheduler(app) -> None:
    """在 Flask 进程内启动定时任务。"""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _run_pipeline_safe,
        _PIPELINE_CRON,
        id="pipeline_full",
        replace_existing=True,
        max_instances=1,       # 同一任务不并发
        coalesce=True,         # 错过多次只补跑一次
        misfire_grace_time=1800,
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    # ── 启动补跑 ──────────────────────────────────────────────────────────
    # 服务刚起来时，距离下一个 cron 时刻最长 ~1 小时，期间数据可能过旧。
    # 在后台线程跑一次完整流水线，包含取消/延期场次，保证页面立即可用。
    def _bootstrap():
        logger.info("⏱️ [启动补跑] 开始完整流水线...")
        _run_pipeline_safe(include_cancelled=True)
        logger.info("✅ [启动补跑] 结束")

    threading.Thread(target=_bootstrap, daemon=True, name="bootstrap-pipeline").start()

    app.logger.info("📅 定时任务已注册:")
    app.logger.info("   • pipeline_full  cron='每小时第5分钟'  — 完整流水线（赛程+赔率+ML+比分+结果）")


def run_pipeline_once(include_cancelled: bool = False) -> dict:
    """
    Admin API 手动触发：同步调用一次完整流水线。
    直接复用调度路径，不 fork 子进程，不返回 subprocess.CompletedProcess。

    Returns
    -------
    dict  形如 `run_pipeline` 的 summary，调用方应读取 `_meta`。
    """
    return _run_pipeline_safe(include_cancelled=include_cancelled)
