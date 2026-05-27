# -*- coding: utf-8 -*-
"""后台定时同步任务（APScheduler）。"""
import atexit
import logging
import subprocess
import sys
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger('match predictor.scheduler')


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
            pass
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
            pass
        except Exception as e:
            logger.error('❌ [定时] 结果完整同步异常: %s', e)

    def job_sync_upcoming():
        try:
            result = _run_script(['scripts/sync_upcoming.py', '--days', '14'], 600)
            if result.returncode == 0:
                out = result.stdout.strip()
                if out and ('插入' in out or '更新' in out or '赔率' in out):
                    logger.info('✅ [定时] 赛程+赔率已同步')
            elif result.stderr.strip():
                logger.error('❌ [定时] 赛程同步失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.error('❌ [定时] 赛程同步异常: %s', e)

    def job_sync_daily_matches():
        try:
            result = _run_script(['scripts/sync_daily_matches.py'], 120)
            if result.returncode == 0:
                out = result.stdout.strip()
                if out and '插入' in out and '0 场' not in out:
                    logger.info('✅ [定时] 彩票赛事已更新: %s', out[:120])
            elif result.stderr.strip():
                logger.error('❌ [定时] 彩票赛事同步失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.error('❌ [定时] 彩票赛事同步异常: %s', e)

    def job_eval_snapshot():
        try:
            result = _run_script(['scripts/eval_snapshot.py', '--days', '30'], 120)
            if result.returncode == 0:
                logger.info('✅ [定时] ML 评估快照已更新')
            elif result.stderr.strip():
                logger.error('❌ [定时] 评估快照失败: %s', result.stderr.strip()[:200])
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.error('❌ [定时] 评估快照异常: %s', e)

    scheduler.add_job(job_sync_results, IntervalTrigger(minutes=10),
                      id='sync_results', replace_existing=True)
    scheduler.add_job(job_sync_results_full, IntervalTrigger(minutes=60),
                      id='sync_results_full', replace_existing=True)
    scheduler.add_job(job_sync_upcoming, IntervalTrigger(minutes=60),
                      id='sync_upcoming', replace_existing=True)
    scheduler.add_job(job_sync_daily_matches, IntervalTrigger(minutes=10),
                      id='sync_daily_matches', replace_existing=True)
    scheduler.add_job(job_eval_snapshot, IntervalTrigger(hours=6),
                      id='eval_snapshot', replace_existing=True)

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    app.logger.info('📅 定时任务已启动:')
    app.logger.info('   • sync_results       每10分钟 — 比赛结果')
    app.logger.info('   • sync_results_full  每60分钟 — 含取消/延期')
    app.logger.info('   • sync_upcoming      每60分钟 — 赛程+赔率')
    app.logger.info('   • sync_daily_matches 每10分钟 — 彩票赛事')
    app.logger.info('   • eval_snapshot      每6小时  — ML 评估快照')


def run_sync_upcoming_once(env: dict | None = None) -> subprocess.CompletedProcess:
    """Admin API 使用的手动同步封装。"""
    python_bin = sys.executable
    return subprocess.run(
        [python_bin, 'scripts/sync_upcoming.py', '--days', '14'],
        capture_output=True,
        text=True,
        timeout=120,
        env=env or None,
    )
