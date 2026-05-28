# -*- coding: utf-8 -*-
"""
后台定时任务（APScheduler）
──────────────────────────
使用统一的流水线（matchpredict.pipeline）替代原来分散的多个 subprocess 脚本。

任务表：
  pipeline_full    — 每 60 分钟  — 完整流水线（赛程→赔率→ML→泊松→结果）
  pipeline_results — 每 10 分钟  — 仅结果同步（轻量，覆盖刚完赛的比赛）

Admin 手动触发接口：run_sync_upcoming_once()（向前兼容，实际调用 full 流水线）
"""
import atexit
import logging
import threading
import sys
import subprocess

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("matchpredict.scheduler")


def setup_scheduler(app) -> None:
    """在 Flask 进程内启动定时任务。"""
    scheduler = BackgroundScheduler()

    def _run_pipeline(mode: str, days_ahead: int = 7, days_back: int = 7,
                      include_cancelled: bool = False):
        """在调度线程内直接调用流水线（比 subprocess 少一次解释器启动开销）。"""
        try:
            from matchpredict.pipeline.runner import run_pipeline, _setup_logging
            _setup_logging()
            result = run_pipeline(
                mode=mode,
                days_ahead=days_ahead,
                days_back=days_back,
                include_cancelled=include_cancelled,
            )
            errors = result.get("_meta", {}).get("total_errors", 0)
            processed = result.get("_meta", {}).get("total_processed", 0)
            if errors:
                logger.warning("⚠️ [%s] 流水线完成，%d 个错误 / %d 条处理",
                               mode, errors, processed)
            elif processed:
                logger.info("✅ [%s] 流水线完成，处理 %d 条", mode, processed)
        except Exception as e:
            logger.error("❌ [%s] 流水线异常: %s", mode, e, exc_info=True)

    def job_full():
        """完整流水线：拉取赛程 + 赔率 + ML预测 + 泊松比分 + 回填结果。"""
        _run_pipeline("full", days_ahead=7, days_back=7)

    def job_results():
        """仅结果回填：轻量任务，每10分钟跑，追踪刚完赛的比赛。"""
        _run_pipeline("results", days_back=3)

    def job_results_full():
        """每小时全量结果回填：包含取消/延期场次。"""
        _run_pipeline("results", days_back=7, include_cancelled=True)

    # ── 注册定时任务 ──────────────────────────────────────────────────────
    # max_instances=1  保证同一任务不并发
    # coalesce=True    多次错过只补跑一次
    # misfire_grace_time 错过窗口内仍可补跑

    scheduler.add_job(
        job_full,
        IntervalTrigger(minutes=60),
        id="pipeline_full",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    scheduler.add_job(
        job_results,
        IntervalTrigger(minutes=10),
        id="pipeline_results",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        job_results_full,
        IntervalTrigger(minutes=60),
        id="pipeline_results_full",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    # ── 服务启动时立即补跑一次，避免第一个定时窗口前数据过旧 ─────────────
    def _bootstrap():
        logger.info("⏱️ [启动补跑] 开始完整流水线...")
        try:
            _run_pipeline("full", days_ahead=7, days_back=7, include_cancelled=True)
            logger.info("✅ [启动补跑] 完成")
        except Exception as e:
            logger.error("❌ [启动补跑] 异常: %s", e)

    threading.Thread(target=_bootstrap, daemon=True, name="bootstrap-sync").start()

    app.logger.info("📅 定时任务已启动:")
    app.logger.info("   • pipeline_full         每60分钟 — 完整流水线（赛程+赔率+ML+比分+结果）")
    app.logger.info("   • pipeline_results      每10分钟 — 轻量结果同步（近3天）")
    app.logger.info("   • pipeline_results_full 每60分钟 — 全量结果同步（含取消/延期）")


def run_sync_upcoming_once(env: dict | None = None) -> subprocess.CompletedProcess:
    """
    Admin API 使用的手动触发接口（向前兼容）。
    直接在当前进程内调用流水线，不再 fork 子进程。
    返回一个模拟的 CompletedProcess（兼容原调用方检查 returncode）。
    """
    try:
        from matchpredict.pipeline.runner import run_pipeline, _setup_logging
        _setup_logging()
        result = run_pipeline(mode="full", days_ahead=7, days_back=7)
        errors = result.get("_meta", {}).get("total_errors", 0)
        rc = 0 if errors == 0 else 1
        out = f"completed: processed={result['_meta']['total_processed']}, errors={errors}"
    except Exception as e:
        rc = 1
        out = str(e)

    # 构造兼容 subprocess.CompletedProcess 的对象
    cp = subprocess.CompletedProcess(
        args=["pipeline", "--mode", "full"],
        returncode=rc,
        stdout=out,
        stderr="",
    )
    return cp
