# -*- coding: utf-8 -*-
"""
流水线编排器
────────────
将 steps.py 中的步骤按顺序串联，形成完整的数据同步 → 预测 → 结果验证流水线：

  Step 1  拉取赛程（football-data + 竞彩让一球）
  Step 2  同步赔率（the-odds-api，赔率有变化时才写库）
  Step 3  ML 概率预测（优先处理赔率刚变化的比赛）
  Step 4  泊松比分预测（由 ML 概率 / 赔率反推预测比分）
  Step 5  回填比赛结果（已完赛 → 比分、命中率、ml_predicted_result）

运行模式（mode 参数）：
  "full"       — 全部 5 步（默认，定时器每小时触发）
  "results"    — 只跑 Step 5（每10分钟，轻量更新结果）

可直接用 CLI 调用：
  python -m matchpredict.pipeline.runner
  python -m matchpredict.pipeline.runner --mode results --days 3
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from matchpredict.pipeline.config import LEAGUES, SYNC_WINDOW_DAYS, RESULT_WINDOW_DAYS
from matchpredict.pipeline import steps

logger = logging.getLogger("matchpredict.pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# 核心流水线
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    mode: str = "full",
    days_ahead: int = SYNC_WINDOW_DAYS,
    days_back: int = RESULT_WINDOW_DAYS,
    leagues: list[str] | None = None,
    include_cancelled: bool = False,
) -> dict:
    """
    执行同步流水线并返回汇总结果。

    Parameters
    ----------
    mode            : "full" | "results"
    days_ahead      : 向未来同步多少天的赛程
    days_back       : 向过去回填多少天的结果
    leagues         : 联赛代码列表，None 使用全局配置
    include_cancelled: 是否同步取消/延期场次

    Returns
    -------
    dict  各步骤结果的汇总
    """
    if leagues is None:
        leagues = LEAGUES

    t_start = time.monotonic()
    sep = "=" * 65

    logger.info(sep)
    mode_cn = "完整模式" if mode == "full" else "结果回填模式"
    logger.info(
        "🚀 流水线开始  模式=%s  联赛数量=%d  赛程前瞻=%d天  结果回溯=%d天",
        mode_cn, len(leagues), days_ahead, days_back
    )
    logger.info(sep)

    summary: dict[str, dict] = {}

    # ── Step 1-4: 赛程 + 赔率 + 预测（"full" 模式专属）────────────────────
    if mode == "full":
        # 步骤 1：拉取赛程
        r1 = steps.step_fetch_fixtures(db=_get_db(), leagues=leagues, days_ahead=days_ahead)
        summary["fetch_fixtures"] = r1

        # 步骤 2：同步赔率（返回赔率有变化的 fixture_id 集合）
        r2 = steps.step_sync_odds(db=_get_db())
        summary["sync_odds"] = r2
        changed_ids: set[str] = r2.get("changed_ids", set())

        # 步骤 3：ML 概率预测（只预测赔率变化 + 从未预测过的场次）
        if r2.get("processed", 0) > 0 or True:   # 总是尝试（可能有新赛程无概率）
            r3 = steps.step_ml_predict(db=_get_db(), changed_ids=changed_ids)
        else:
            logger.info("━━━ Step 3: ML 概率预测 (跳过，无赔率变化) ━━━")
            r3 = steps.step_ml_predict(db=_get_db(), changed_ids=None)
        summary["ml_predict"] = r3

        # 步骤 4：泊松比分（依赖 Step 3 写入的概率）
        r4 = steps.step_poisson_scores(db=_get_db(), days_back=max(days_ahead, 14))
        summary["poisson_scores"] = r4

    # ── Step 5: 回填已完赛结果（"full" + "results" 都跑）─────────────────
    r5 = steps.step_backfill_results(
        db=_get_db(),
        leagues=leagues,
        days_back=days_back,
        include_cancelled=include_cancelled,
    )
    summary["backfill_results"] = r5

    # ── 汇总 ──────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t_start
    total_processed = sum(v.get("processed", 0) for v in summary.values())
    total_errors = sum(v.get("errors", 0) for v in summary.values())

    logger.info(sep)
    logger.info("✅ 流水线完成  耗时=%.1fs  总处理=%d  错误=%d",
                elapsed, total_processed, total_errors)

    # 打印每步摘要
    step_names = {
        "fetch_fixtures": "Step1 赛程",
        "sync_odds":      "Step2 赔率",
        "ml_predict":     "Step3 ML",
        "poisson_scores": "Step4 泊松",
        "backfill_results": "Step5 结果",
    }
    for key, label in step_names.items():
        if key not in summary:
            continue
        v = summary[key]
        logger.info(
            "   %-12s 处理=%-4d 跳过=%-4d 错误=%d",
            label, v.get("processed", 0), v.get("skipped", 0), v.get("errors", 0)
        )
    logger.info(sep)

    summary["_meta"] = {"elapsed_s": elapsed, "total_processed": total_processed,
                        "total_errors": total_errors}
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# 懒加载数据库连接
# ─────────────────────────────────────────────────────────────────────────────

_db_instance = None

def _get_db():
    global _db_instance
    if _db_instance is None:
        from matchpredict.db import prediction_db
        _db_instance = prediction_db
    return _db_instance


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 压低第三方库噪声
    for noisy in ("urllib3", "requests", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Match Predictor 数据同步流水线")
    parser.add_argument("--mode", choices=["full", "results"], default="full",
                        help="full=完整流水线; results=只同步比赛结果 (默认: full)")
    parser.add_argument("--days-ahead", type=int, default=SYNC_WINDOW_DAYS,
                        help=f"向前同步赛程天数 (默认: {SYNC_WINDOW_DAYS})")
    parser.add_argument("--days-back", type=int, default=RESULT_WINDOW_DAYS,
                        help=f"回溯结果天数 (默认: {RESULT_WINDOW_DAYS})")
    parser.add_argument("--leagues", default=None,
                        help="逗号分隔的联赛代码，默认使用 config.LEAGUES")
    parser.add_argument("--with-cancelled", action="store_true",
                        help="同时同步取消/延期场次")
    parser.add_argument("--log-level", default="INFO",
                        help="日志级别 (默认: INFO)")
    args = parser.parse_args()

    _setup_logging(args.log_level)

    leagues = [l.strip() for l in args.leagues.split(",")] if args.leagues else None

    result = run_pipeline(
        mode=args.mode,
        days_ahead=args.days_ahead,
        days_back=args.days_back,
        leagues=leagues,
        include_cancelled=args.with_cancelled,
    )
    sys.exit(0 if result["_meta"]["total_errors"] == 0 else 1)


if __name__ == "__main__":
    main()
