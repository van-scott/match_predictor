#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比赛结果同步脚本
功能：
1. 从 football-data.org 拉取最近已结束比赛的真实比分
2. 回填到 upcoming_fixtures 表的 actual_home_goals / actual_away_goals / actual_result
3. 自动计算 ML 预测准确性（胜平负是否命中、比分偏差）

用法:
  python scripts/sync_results.py             # 同步最近7天已结束比赛
  python scripts/sync_results.py --days 30   # 同步最近30天
"""
import os
import sys
import time
import logging
import argparse
import requests
import math
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY", "d318f21f939e4752a93313937fd203e9")
BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": API_KEY}

LEAGUE_NAMES = {
    "PL":  "英超", "PD":  "西甲", "SA":  "意甲",
    "BL1": "德甲", "FL1": "法甲",
}
DEFAULT_LEAGUES = ["PL", "PD", "SA", "BL1", "FL1"]


def fetch_finished_matches(league_id: str, days_back: int = 7) -> list:
    """拉取指定联赛最近 N 天已结束的比赛"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    url = f"{BASE_URL}/competitions/{league_id}/matches"
    params = {
        "status":   "FINISHED",
        "dateFrom": start.strftime("%Y-%m-%d"),
        "dateTo":   now.strftime("%Y-%m-%d"),
    }
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            matches = resp.json().get("matches", [])
            logger.info(f"  {league_id}: 获取到 {len(matches)} 场已结束比赛")
            return matches
        elif resp.status_code == 429:
            logger.warning("限速，等待60秒...")
            time.sleep(60)
            return fetch_finished_matches(league_id, days_back)
        else:
            logger.warning(f"{league_id} HTTP {resp.status_code}: {resp.text[:200]}")
            return []
    except Exception as e:
        logger.error(f"拉取 {league_id} 失败: {e}")
        return []


def backfill_results(matches: list, league_code: str, db) -> dict:
    """
    将真实比分回填到 upcoming_fixtures，并计算 ML 预测准确性。
    返回统计 {'updated': N, 'correct': N, 'score_hit': N}
    """
    stats = {'updated': 0, 'correct': 0, 'score_hit': 0, 'total_checked': 0}

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            for m in matches:
                fixture_id = str(m.get("id", ""))
                score = m.get("score", {})
                ft = score.get("fullTime", {})
                home_goals = ft.get("home")
                away_goals = ft.get("away")

                if home_goals is None or away_goals is None:
                    continue

                # 实际结果
                if home_goals > away_goals:
                    actual_result = 'H'
                elif home_goals == away_goals:
                    actual_result = 'D'
                else:
                    actual_result = 'A'

                # 先查出该比赛的 ML 预测
                cur.execute("""
                    SELECT ml_home_prob, ml_draw_prob, ml_away_prob,
                           predicted_home_goals, predicted_away_goals
                    FROM upcoming_fixtures
                    WHERE fixture_id = %s
                """, (fixture_id,))
                row = cur.fetchone()

                result_correct = None
                score_correct_val = None
                goal_diff_error = None
                ml_predicted_result = None

                if row and row[0] is not None:
                    ml_h, ml_d, ml_a = float(row[0]), float(row[1]), float(row[2])
                    pred_hg = row[3]
                    pred_ag = row[4]

                    # ML 预测的胜平负
                    if ml_h >= ml_d and ml_h >= ml_a:
                        ml_predicted_result = 'H'
                    elif ml_d >= ml_h and ml_d >= ml_a:
                        ml_predicted_result = 'D'
                    else:
                        ml_predicted_result = 'A'

                    result_correct = (ml_predicted_result == actual_result)
                    stats['total_checked'] += 1
                    if result_correct:
                        stats['correct'] += 1

                    # 比分精确命中
                    if pred_hg is not None and pred_ag is not None:
                        score_correct_val = (pred_hg == home_goals and pred_ag == away_goals)
                        if score_correct_val:
                            stats['score_hit'] += 1
                        goal_diff_error = abs((pred_hg + pred_ag) - (home_goals + away_goals))

                # 更新数据库
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        status = 'FINISHED',
                        actual_home_goals = %s,
                        actual_away_goals = %s,
                        actual_result = %s,
                        finished_at = CURRENT_TIMESTAMP,
                        ml_predicted_result = %s,
                        result_correct = %s,
                        score_correct = %s,
                        goal_diff_error = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fixture_id = %s
                """, (
                    home_goals, away_goals, actual_result,
                    ml_predicted_result, result_correct,
                    score_correct_val, goal_diff_error,
                    fixture_id,
                ))

                if cur.rowcount > 0:
                    stats['updated'] += 1
                    home_team = m.get("homeTeam", {}).get("name", "?")
                    away_team = m.get("awayTeam", {}).get("name", "?")
                    check = '✅' if result_correct else ('❌' if result_correct is False else '⚪')
                    logger.info(
                        f"  {check} {home_team} {home_goals}-{away_goals} {away_team}"
                        f"  ML预测={ml_predicted_result or '无'}  实际={actual_result}"
                    )

            conn.commit()
    except Exception as e:
        logger.error(f"回填结果失败: {e}", exc_info=True)

    return stats


def generate_predicted_scores(db):
    """
    为有 ML 概率但无预测比分的比赛，用泊松模型推算预测比分。
    """
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, ml_home_prob, ml_away_prob
                FROM upcoming_fixtures
                WHERE ml_home_prob IS NOT NULL
                  AND predicted_home_goals IS NULL
            """)
            rows = cur.fetchall()
            updated = 0
            for fix_id, ml_h, ml_a in rows:
                ph, pa = float(ml_h), float(ml_a)
                # 简单泊松均值 → 预测比分
                lam_h = max(-math.log(max(1 - ph, 0.01)) * 1.5, 0.3)
                lam_a = max(-math.log(max(1 - pa, 0.01)) * 1.5, 0.3)
                pred_h = min(round(lam_h), 5)
                pred_a = min(round(lam_a), 5)
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        predicted_home_goals = %s,
                        predicted_away_goals = %s
                    WHERE fixture_id = %s
                """, (pred_h, pred_a, fix_id))
                updated += 1
            conn.commit()
            if updated:
                logger.info(f"📊 为 {updated} 场比赛生成了预测比分")
    except Exception as e:
        logger.error(f"生成预测比分失败: {e}")


def print_accuracy_summary(db):
    """打印总体准确率统计"""
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            # 总体统计
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE actual_result IS NOT NULL) AS total_finished,
                    COUNT(*) FILTER (WHERE result_correct IS NOT NULL) AS total_predicted,
                    COUNT(*) FILTER (WHERE result_correct = true) AS correct,
                    COUNT(*) FILTER (WHERE score_correct = true) AS score_hit,
                    AVG(goal_diff_error) FILTER (WHERE goal_diff_error IS NOT NULL) AS avg_goal_err
                FROM upcoming_fixtures
            """)
            r = cur.fetchone()
            total_fin, total_pred, correct, score_hit, avg_err = r

            print("\n" + "=" * 60)
            print("📊 预测准确率总览")
            print("=" * 60)
            print(f"  已结束比赛:     {total_fin} 场")
            print(f"  有ML预测的:     {total_pred} 场")
            if total_pred > 0:
                acc = correct / total_pred * 100
                print(f"  胜平负命中:     {correct}/{total_pred} = {acc:.1f}%")
                print(f"  比分精确命中:   {score_hit}/{total_pred}")
                if avg_err is not None:
                    print(f"  平均进球偏差:   {avg_err:.2f} 球")

            # 分联赛统计
            cur.execute("""
                SELECT league_name,
                    COUNT(*) FILTER (WHERE result_correct IS NOT NULL) AS predicted,
                    COUNT(*) FILTER (WHERE result_correct = true) AS correct
                FROM upcoming_fixtures
                WHERE actual_result IS NOT NULL
                GROUP BY league_name
                ORDER BY league_name
            """)
            rows = cur.fetchall()
            if rows:
                print(f"\n{'联赛':<8} {'预测':>4} {'命中':>4} {'准确率':>8}")
                print("-" * 30)
                for lg, pred, corr in rows:
                    if pred > 0:
                        print(f"  {lg:<6} {pred:>4} {corr:>4} {corr/pred*100:>7.1f}%")

            print("=" * 60)
    except Exception as e:
        logger.error(f"打印统计失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="同步已结束比赛真实比分 + 计算预测准确率")
    parser.add_argument("--leagues", default=",".join(DEFAULT_LEAGUES))
    parser.add_argument("--days", type=int, default=7, help="回溯天数")
    args = parser.parse_args()

    from scripts.database import prediction_db as db

    leagues = [l.strip() for l in args.leagues.split(",")]

    print("=" * 60)
    print("🔄 同步已结束比赛结果 + 验证预测准确率")
    print(f"   联赛: {', '.join(leagues)}  |  回溯 {args.days} 天")
    print("=" * 60)

    # 先生成预测比分
    generate_predicted_scores(db)

    total_stats = {'updated': 0, 'correct': 0, 'score_hit': 0, 'total_checked': 0}

    for league_id in leagues:
        logger.info(f"📥 {LEAGUE_NAMES.get(league_id, league_id)} ({league_id})")
        raw = fetch_finished_matches(league_id, args.days)
        if raw:
            s = backfill_results(raw, league_id, db)
            for k in total_stats:
                total_stats[k] += s[k]
        time.sleep(7)  # 免费版限速

    print(f"\n✅ 同步完成: 更新 {total_stats['updated']} 场")
    if total_stats['total_checked'] > 0:
        acc = total_stats['correct'] / total_stats['total_checked'] * 100
        print(f"   本次命中率: {total_stats['correct']}/{total_stats['total_checked']} = {acc:.1f}%")
        print(f"   比分精确命中: {total_stats['score_hit']} 场")

    print_accuracy_summary(db)


if __name__ == "__main__":
    main()
