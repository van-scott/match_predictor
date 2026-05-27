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

# 加载 .env 文件（确保直接运行脚本时也能读取环境变量）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                key, val = key.strip(), val.strip()
                if key and key not in os.environ:
                    os.environ[key] = val

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
    "CL":  "欧冠", "CLI": "解放者杯", "BSA": "巴甲",
}
DEFAULT_LEAGUES = ["PL", "PD", "SA", "BL1", "FL1", "CL", "CLI", "BSA"]


def fetch_finished_matches(league_id: str, days_back: int = 7,
                           include_cancelled: bool = False) -> list:
    """拉取指定联赛最近 N 天已结束（以及可选的取消/延期）的比赛"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    url = f"{BASE_URL}/competitions/{league_id}/matches"
    status_filters = ["FINISHED"]
    if include_cancelled:
        status_filters += ["CANCELLED", "POSTPONED"]

    all_matches = []
    for status_filter in status_filters:
        params = {
            "status":   status_filter,
            "dateFrom": start.strftime("%Y-%m-%d"),
            "dateTo":   now.strftime("%Y-%m-%d"),
        }
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                all_matches.extend(resp.json().get("matches", []))
            elif resp.status_code == 429:
                logger.warning("限速，等待60秒...")
                time.sleep(60)
                resp2 = requests.get(url, headers=HEADERS, params=params, timeout=15)
                if resp2.status_code == 200:
                    all_matches.extend(resp2.json().get("matches", []))
            # 403/404 → skip silently (league not on free plan)
        except Exception as e:
            logger.error(f"拉取 {league_id} ({status_filter}) 失败: {e}")
    logger.info(f"  {league_id}: 获取到 {len(all_matches)} 场比赛")
    return all_matches


def _sync_cancelled_postponed(matches: list, db) -> int:
    """将取消/延期比赛的状态更新到数据库（无需计算准确率）"""
    updated = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for m in matches:
                status = m.get("status", "")
                if status not in ("CANCELLED", "POSTPONED"):
                    continue
                fixture_id = str(m.get("id", ""))
                cur.execute("""
                    UPDATE upcoming_fixtures SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE fixture_id = %s AND status NOT IN ('FINISHED')
                """, (status, fixture_id))
                if cur.rowcount > 0:
                    updated += 1
            conn.commit()
    except Exception as e:
        logger.error(f"更新取消/延期比赛失败: {e}")
    return updated


def sync_dm_results(db) -> int:
    """
    同步 dm_ 前缀（彩票来源）的已过时间比赛的实际结果。
    优先用 OpenLigaDB（德国各级联赛免费）匹配，未来可扩展其他来源。
    仅更新 status='SCHEDULED'/'TIMED' 且 match_time < NOW() 的比赛。
    """
    updated = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            # 查所有 dm_ 且已过时间但无实际结果的比赛
            cur.execute("""
                SELECT fixture_id, league_code, home_team, away_team, match_time
                FROM upcoming_fixtures
                WHERE fixture_id LIKE 'dm_%'
                  AND actual_result IS NULL
                  AND match_time < NOW() - INTERVAL '3 hours'
                ORDER BY match_time DESC LIMIT 50
            """)
            pending = cur.fetchall()
            if not pending:
                return 0

            # 从 OpenLigaDB 拉取近期德国联赛结果（免费，覆盖 BL1/BL2/BL3/rel）
            ol_results = {}  # (team1_norm, team2_norm) -> (s1, s2)
            for league_shortcut in ('bl1', 'bl2', 'bl3', 'rel'):
                try:
                    resp = requests.get(
                        f"https://api.openligadb.de/getmatchdata/{league_shortcut}/2025",
                        timeout=10
                    )
                    if resp.status_code != 200:
                        continue
                    for m in resp.json():
                        if not m.get('matchIsFinished'):
                            continue
                        t1 = m.get('team1', {}).get('teamName', '').lower()
                        t2 = m.get('team2', {}).get('teamName', '').lower()
                        results = m.get('matchResults', [])
                        final = next((r for r in results if r.get('resultTypeID') == 2), None)
                        if final:
                            ol_results[(t1, t2)] = (final['pointsTeam1'], final['pointsTeam2'])
                except Exception:
                    pass

            def _norm(name: str) -> str:
                import unicodedata
                s = unicodedata.normalize('NFKD', name)
                s = ''.join(c for c in s if not unicodedata.combining(c))
                # Remove common prefixes/suffixes
                import re
                s = re.sub(r'^(FC|SC|VfB|VfL|SpVgg|SSV|SV|TSV|RB|1\.|Rot.Weiss)\s+', '', s, flags=re.I)
                s = re.sub(r'\s+(FC|SC|e\.V\.)$', '', s, flags=re.I)
                return s.strip().lower()

            for fid, lc, home, away, match_time in pending:
                home_n, away_n = _norm(home), _norm(away)
                score = None
                # Try exact match in OpenLigaDB results
                for (t1, t2), (s1, s2) in ol_results.items():
                    if (home_n in t1 or t1 in home_n) and (away_n in t2 or t2 in away_n):
                        score = (s1, s2)
                        break
                    if (away_n in t1 or t1 in away_n) and (home_n in t2 or t2 in home_n):
                        score = (s2, s1)  # reversed
                        break
                if score is None:
                    continue
                hg, ag = score
                ar = 'H' if hg > ag else ('D' if hg == ag else 'A')
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        status = 'FINISHED', actual_home_goals = %s, actual_away_goals = %s,
                        actual_result = %s, finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fixture_id = %s
                """, (hg, ag, ar, fid))
                if cur.rowcount > 0:
                    logger.info(f"  ✅ dm 结果 {home} {hg}-{ag} {away}")
                    updated += 1
            conn.commit()
    except Exception as e:
        logger.error(f"同步 dm_ 比赛结果失败: {e}", exc_info=True)
    return updated


def backfill_results(matches: list, league_code: str, db) -> dict:
    """
    将真实比分回填到 upcoming_fixtures，并计算 ML 预测准确性。
    如果 fixture_id 不在数据库中，则插入一条已结束的记录（无 ML 预测对比，但有真实比分）。
    返回统计 {'updated': N, 'inserted': N, 'correct': N, 'score_hit': N}
    """
    stats = {'updated': 0, 'inserted': 0, 'correct': 0, 'score_hit': 0, 'total_checked': 0}

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            for m in matches:
                if m.get("status") in ("CANCELLED", "POSTPONED"):
                    continue  # 取消/延期由 _sync_cancelled_postponed 处理
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

                home_team = m.get("homeTeam", {}).get("name", "?")
                away_team = m.get("awayTeam", {}).get("name", "?")
                utc_date = m.get("utcDate", "")
                match_time = None
                if utc_date:
                    try:
                        match_time = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                    except Exception:
                        pass
                matchday = m.get("matchday")

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

                    # 如果没有预测比分，用泊松模型从概率推算
                    if pred_hg is None or pred_ag is None:
                        lam_h = max(-math.log(max(1 - ml_h, 0.01)) * 1.5, 0.3)
                        lam_a = max(-math.log(max(1 - ml_a, 0.01)) * 1.5, 0.3)
                        pred_hg = min(round(lam_h), 5)
                        pred_ag = min(round(lam_a), 5)

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
                        # E3-fix: 计算净胜球预测误差（正确语义）
                        # pred_goal_diff = pred_hg - pred_ag; actual_goal_diff = home_goals - away_goals
                        goal_diff_error = abs((pred_hg - pred_ag) - (home_goals - away_goals))

                if row is not None:
                    # fixture_id 已存在 → UPDATE
                    cur.execute("""
                        UPDATE upcoming_fixtures SET
                            status = 'FINISHED',
                            actual_home_goals = %s,
                            actual_away_goals = %s,
                            actual_result = %s,
                            finished_at = CURRENT_TIMESTAMP,
                            ml_predicted_result = %s,
                            predicted_home_goals = COALESCE(predicted_home_goals, %s),
                            predicted_away_goals = COALESCE(predicted_away_goals, %s),
                            result_correct = %s,
                            score_correct = %s,
                            goal_diff_error = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE fixture_id = %s
                    """, (
                        home_goals, away_goals, actual_result,
                        ml_predicted_result,
                        pred_hg, pred_ag,
                        result_correct,
                        score_correct_val, goal_diff_error,
                        fixture_id,
                    ))
                    if cur.rowcount > 0:
                        stats['updated'] += 1
                        check = '✅' if result_correct else ('❌' if result_correct is False else '⚪')
                        logger.info(
                            f"  {check} {home_team} {home_goals}-{away_goals} {away_team}"
                            f"  ML预测={ml_predicted_result or '无'}  实际={actual_result}"
                        )
                else:
                    # fixture_id 不存在 → INSERT（无 ML 预测，但有真实比分）
                    cur.execute("""
                        INSERT INTO upcoming_fixtures (
                            fixture_id, league_code, league_name,
                            home_team, away_team, match_time, matchday,
                            status, actual_home_goals, actual_away_goals, actual_result,
                            finished_at, synced_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'FINISHED', %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ON CONFLICT (fixture_id) DO NOTHING
                    """, (
                        fixture_id, league_code, LEAGUE_NAMES.get(league_code, league_code),
                        home_team, away_team, match_time, matchday,
                        home_goals, away_goals, actual_result,
                    ))
                    if cur.rowcount > 0:
                        stats['inserted'] += 1
                        logger.info(
                            f"  🆕 {home_team} {home_goals}-{away_goals} {away_team}"
                            f"  (新插入，无ML预测对比)"
                        )

            conn.commit()
    except Exception as e:
        logger.error(f"回填结果失败: {e}", exc_info=True)

    return stats


def generate_predicted_scores(db):
    """
    为有 ML 概率（或赔率）但无预测比分/概率的比赛，推算 ML 概率和预测比分。
    对于 dm_ 前缀的彩票来源比赛，从赔率推算隐含概率，使其能进入预测回顾。
    """
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            # 1. 有 ML 概率但无预测比分 → 推算比分
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
                lam_h = max(-math.log(max(1 - ph, 0.01)) * 1.5, 0.3)
                lam_a = max(-math.log(max(1 - pa, 0.01)) * 1.5, 0.3)
                pred_h = min(round(lam_h), 5)
                pred_a = min(round(lam_a), 5)
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        predicted_home_goals = %s, predicted_away_goals = %s
                    WHERE fixture_id = %s
                """, (pred_h, pred_a, fix_id))
                updated += 1

            # 2. 无 ML 概率但有赔率 → 从赔率推算隐含概率（用于彩票来源 dm_ 比赛）
            cur.execute("""
                SELECT fixture_id, home_odds, draw_odds, away_odds
                FROM upcoming_fixtures
                WHERE ml_home_prob IS NULL
                  AND home_odds IS NOT NULL AND draw_odds IS NOT NULL AND away_odds IS NOT NULL
                  AND match_time < NOW()
            """)
            odds_rows = cur.fetchall()
            for fix_id, ho, do_, ao in odds_rows:
                try:
                    rh, rd, ra = 1/float(ho), 1/float(do_), 1/float(ao)
                    total = rh + rd + ra
                    ph, pd, pa = rh/total, rd/total, ra/total
                    lam_h = max(-math.log(max(1-ph, 0.01))*1.5, 0.3)
                    lam_a = max(-math.log(max(1-pa, 0.01))*1.5, 0.3)
                    pred_h = min(round(lam_h), 5)
                    pred_a = min(round(lam_a), 5)
                    cur.execute("""
                        UPDATE upcoming_fixtures SET
                            ml_home_prob = %s, ml_draw_prob = %s, ml_away_prob = %s,
                            predicted_home_goals = COALESCE(predicted_home_goals, %s),
                            predicted_away_goals = COALESCE(predicted_away_goals, %s)
                        WHERE fixture_id = %s
                    """, (ph, pd, pa, pred_h, pred_a, fix_id))
                    updated += 1
                except Exception:
                    pass

            conn.commit()
            if updated:
                logger.info(f"📊 为 {updated} 场比赛生成/补全了预测概率和比分")
    except Exception as e:
        logger.error(f"生成预测比分失败: {e}")


def _backfill_ml_predicted_result(db):
    """补全：有实际结果 + 有 ML 概率但缺 ml_predicted_result 的比赛"""
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, ml_home_prob, ml_draw_prob, ml_away_prob,
                       predicted_home_goals, predicted_away_goals,
                       actual_home_goals, actual_away_goals, actual_result
                FROM upcoming_fixtures
                WHERE actual_result IS NOT NULL
                  AND ml_home_prob IS NOT NULL
                  AND ml_predicted_result IS NULL
            """)
            rows = cur.fetchall()
            fixed = 0
            for r in rows:
                fid, mlh, mld, mla, phg, pag, ahg, aag, ar = r
                mlh, mld, mla = float(mlh), float(mld), float(mla)
                if mlh >= mld and mlh >= mla:
                    ml_pred = 'H'
                elif mld >= mlh and mld >= mla:
                    ml_pred = 'D'
                else:
                    ml_pred = 'A'
                if phg is None:
                    lam_h = max(-math.log(max(1-mlh, 0.01))*1.5, 0.3)
                    phg = min(round(lam_h), 5)
                if pag is None:
                    lam_a = max(-math.log(max(1-mla, 0.01))*1.5, 0.3)
                    pag = min(round(lam_a), 5)
                rc = (ml_pred == ar)
                sc = (phg == int(ahg) and pag == int(aag)) if ahg is not None else None
                gde = abs((phg + pag) - (int(ahg) + int(aag))) if ahg is not None else None
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        ml_predicted_result = %s, result_correct = %s,
                        score_correct = %s, goal_diff_error = %s,
                        predicted_home_goals = COALESCE(predicted_home_goals, %s),
                        predicted_away_goals = COALESCE(predicted_away_goals, %s)
                    WHERE fixture_id = %s
                """, (ml_pred, rc, sc, gde, phg, pag, fid))
                fixed += 1
            conn.commit()
            if fixed:
                logger.info(f"📊 补全 {fixed} 场比赛的 ml_predicted_result")
    except Exception as e:
        logger.error(f"补全 ml_predicted_result 失败: {e}")


def print_accuracy_summary(db):
    """打印总体准确率统计"""
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            # 总体统计
            # A4-fix: total_finished 统一口径 — 同时要求有实际结果 AND ML 预测
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE actual_result IS NOT NULL AND ml_predicted_result IS NOT NULL
                    ) AS total_finished,
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
                WHERE actual_result IS NOT NULL AND ml_predicted_result IS NOT NULL
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
    parser.add_argument("--with-cancelled", action="store_true",
                        help="同时同步取消/延期比赛状态（每小时跑一次即可）")
    args = parser.parse_args()

    from scripts.database import prediction_db as db

    leagues = [l.strip() for l in args.leagues.split(",")]

    print("=" * 60)
    print("🔄 同步已结束比赛结果 + 验证预测准确率")
    print(f"   联赛: {', '.join(leagues)}  |  回溯 {args.days} 天")
    print("=" * 60)

    # 先生成预测比分（含从赔率推算概率）
    generate_predicted_scores(db)

    # 同步彩票来源(dm_)的比赛结果（OpenLigaDB for German leagues）
    dm_updated = sync_dm_results(db)
    if dm_updated:
        logger.info(f"✅ dm_ 比赛结果同步: {dm_updated} 场")

    # 补全：有实际结果 + 有概率 但缺 ml_predicted_result 的比赛
    _backfill_ml_predicted_result(db)

    total_stats = {'updated': 0, 'inserted': 0, 'correct': 0, 'score_hit': 0, 'total_checked': 0}

    for league_id in leagues:
        logger.info(f"📥 {LEAGUE_NAMES.get(league_id, league_id)} ({league_id})")
        raw = fetch_finished_matches(league_id, args.days,
                                     include_cancelled=args.with_cancelled)
        if raw:
            if args.with_cancelled:
                cancelled = _sync_cancelled_postponed(raw, db)
                if cancelled:
                    logger.info(f"  {league_id}: 标记 {cancelled} 场取消/延期")
            s = backfill_results(raw, league_id, db)
            for k in total_stats:
                total_stats[k] += s[k]
        time.sleep(6)  # 免费版限速（8联赛 × 6s ≈ 48s，节省API额度）

    print(f"\n✅ 同步完成: 更新 {total_stats['updated']} 场, 新插入 {total_stats['inserted']} 场")
    if total_stats['total_checked'] > 0:
        acc = total_stats['correct'] / total_stats['total_checked'] * 100
        print(f"   本次命中率: {total_stats['correct']}/{total_stats['total_checked']} = {acc:.1f}%")
        print(f"   比分精确命中: {total_stats['score_hit']} 场")

    print_accuracy_summary(db)

    try:
        from matchpredict.services.eval_service import eval_service
        eval_service.run_and_save(days=30)
    except Exception as e:
        logger.warning('评估快照写入失败: %s', e)


if __name__ == "__main__":
    main()
