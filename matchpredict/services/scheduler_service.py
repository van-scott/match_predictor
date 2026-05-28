# -*- coding: utf-8 -*-
"""后台定时同步任务（APScheduler）。"""
import atexit
import logging
import subprocess
import sys
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger('matchpredict.scheduler')


def setup_scheduler(app) -> None:
    """在 Flask 进程内启动数据同步定时任务。"""

    python_bin = sys.executable
    scheduler = BackgroundScheduler()

    def _run_script(args, timeout):
        return subprocess.run(
            [python_bin] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def job_sync_results():
        try:
            result = _run_script(['scripts/sync_results.py', '--days', '7'], 300)
            if result.returncode == 0:
                if '更新 0 场' not in result.stdout or '新插入 0 场' not in result.stdout:
                    logger.info('✅ [定时] 比赛结果已同步')
            elif result.stderr.strip() and 'No matches' not in result.stderr:
                logger.error('❌ [定时] 同步失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            logger.warning('⚠️ [定时] 比赛结果同步超时 (300s)，跳过本次')
        except Exception as e:
            logger.error('❌ [定时] 同步异常: %s', e)

    def job_sync_results_full():
        try:
            result = _run_script(
                ['scripts/sync_results.py', '--days', '7', '--with-cancelled'], 360
            )
            if result.returncode != 0 and result.stderr.strip():
                logger.error('❌ [定时] 结果完整同步失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            logger.warning('⚠️ [定时] 结果完整同步超时 (360s)，跳过本次')
        except Exception as e:
            logger.error('❌ [定时] 结果完整同步异常: %s', e)

    def job_sync_upcoming():
        try:
            result = _run_script(['scripts/sync_upcoming.py', '--days', '7'], 600)
            if result.returncode == 0:
                out = result.stdout.strip()
                if out and ('插入' in out or '更新' in out or '赔率' in out):
                    logger.info('✅ [定时] 赛程+赔率已同步')
            elif result.stderr.strip():
                logger.error('❌ [定时] 赛程同步失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            logger.warning('⚠️ [定时] 赛程同步超时 (600s)，跳过本次')
        except Exception as e:
            logger.error('❌ [定时] 赛程同步异常: %s', e)

    def job_eval_snapshot():
        try:
            result = _run_script(['scripts/eval_snapshot.py', '--days', '30'], 120)
            if result.returncode == 0:
                logger.info('✅ [定时] ML 评估快照已更新')
            elif result.stderr.strip():
                logger.error('❌ [定时] 评估快照失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            logger.warning('⚠️ [定时] 评估快照超时 (120s)，跳过本次')
        except Exception as e:
            logger.error('❌ [定时] 评估快照异常: %s', e)

    # 防止重入：同一任务未完成时不并发启动；错过窗口后在 grace 时间内补跑。
    # 同步赛事数据，并且用ML模型预测
    scheduler.add_job(
        job_sync_upcoming,
        IntervalTrigger(minutes=60),
        id='sync_upcoming',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    scheduler.add_job(
        job_sync_results,
        IntervalTrigger(minutes=10),
        id='sync_results',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        job_sync_results_full,
        IntervalTrigger(minutes=60),
        id='sync_results_full',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    scheduler.add_job(
        job_eval_snapshot,
        IntervalTrigger(hours=6),
        id='eval_snapshot',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    def run_bootstrap_sync():
        """服务启动后立即补跑一次，避免首次定时窗口前数据过旧。"""
        logger.info('⏱️ [启动补跑] 开始执行赛程/结果同步...')
        try:
            job_sync_upcoming()
            job_sync_results()
            job_sync_results_full()
            job_eval_snapshot()
            logger.info('✅ [启动补跑] 自动同步完成')
        except Exception as e:
            logger.error('❌ [启动补跑] 自动同步异常: %s', e)

    threading.Thread(target=run_bootstrap_sync, daemon=True).start()

    app.logger.info('📅 定时任务已启动:')
    app.logger.info('   • sync_results       每10分钟 — 比赛结果')
    app.logger.info('   • sync_results_full  每60分钟 — 含取消/延期')
    app.logger.info('   • sync_upcoming      每60分钟 — 近7天赛程+赔率+ML（含让一球）')
    app.logger.info('   • eval_snapshot      每6小时  — ML 评估快照')


def run_sync_upcoming_once(env: dict | None = None) -> subprocess.CompletedProcess:
    """Admin API 使用的手动同步封装。"""
    python_bin = sys.executable
    return subprocess.run(
        [python_bin, 'scripts/sync_upcoming.py', '--days', '7'],
        capture_output=True,
        text=True,
        timeout=120,
        env=env or None,
    )
