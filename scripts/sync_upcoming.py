#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未开赛赛程同步脚本
功能：
1. 从 football-data.org 拉取未开赛比赛写入 upcoming_fixtures
2. 批量对每场比赛运行 ML 预测，写入预测概率
3. 同步赔率（有赔率数据时），并追踪开盘 vs 当前变动

用法:
  python scripts/sync_upcoming.py                  # 同步全部联赛
  python scripts/sync_upcoming.py --leagues PL,PD  # 只同步英超+西甲
  python scripts/sync_upcoming.py --days 14        # 拉未来14天内的比赛
"""
import os
import sys
import time
import logging
import argparse
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

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
    "PL":  "英超",
    "PD":  "西甲",
    "SA":  "意甲",
    "BL1": "德甲",
    "FL1": "法甲",
    "CL":  "欧冠",
    "EL":  "欧联",
}

DEFAULT_LEAGUES = ["PL", "PD", "SA", "BL1", "FL1"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 拉取赛程
# ─────────────────────────────────────────────────────────────────────────────

def fetch_upcoming(league_id: str, days_ahead: int = 14) -> list:
    """拉取指定联赛未来 N 天内的未开赛比赛"""
    now  = datetime.now(timezone.utc)
    end  = now + timedelta(days=days_ahead)
    url  = f"{BASE_URL}/competitions/{league_id}/matches"
    params = {
        "status":    "SCHEDULED,TIMED",
        "dateFrom":  now.strftime("%Y-%m-%d"),
        "dateTo":    end.strftime("%Y-%m-%d"),
    }
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("matches", [])
        elif resp.status_code == 429:
            logger.warning("限速，等待60秒...")
            time.sleep(60)
            return fetch_upcoming(league_id, days_ahead)
        else:
            logger.warning(f"{league_id} HTTP {resp.status_code}")
            return []
    except Exception as e:
        logger.error(f"拉取 {league_id} 失败: {e}")
        return []


def parse_fixture(raw: dict, league_code: str) -> dict:
    """解析原始 API 数据为 upcoming_fixtures 格式"""
    utc_date = raw.get("utcDate", "")
    match_dt = None
    if utc_date:
        try:
            match_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
        except Exception:
            pass

    # 从 API 拿赔率（付费版才有，免费版只有消息）
    odds_raw  = raw.get("odds", {})
    home_odds = None
    draw_odds = None
    away_odds = None
    if isinstance(odds_raw, dict) and "homeWin" in odds_raw:
        home_odds = odds_raw.get("homeWin")
        draw_odds = odds_raw.get("draw")
        away_odds = odds_raw.get("awayWin")

    return {
        "fixture_id":  str(raw.get("id", "")),
        "league_code": league_code,
        "league_name": LEAGUE_NAMES.get(league_code, league_code),
        "home_team":   raw.get("homeTeam", {}).get("name", ""),
        "away_team":   raw.get("awayTeam", {}).get("name", ""),
        "match_time":  match_dt,
        "status":      raw.get("status", "TIMED"),
        "matchday":    raw.get("matchday"),
        "home_odds":   home_odds,
        "draw_odds":   draw_odds,
        "away_odds":   away_odds,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 写入数据库
# ─────────────────────────────────────────────────────────────────────────────

def upsert_fixtures(fixtures: list, db) -> int:
    """批量 UPSERT 赛程数据"""
    if not fixtures:
        return 0
    saved = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for f in fixtures:
                cur.execute("""
                    INSERT INTO upcoming_fixtures (
                        fixture_id, league_code, league_name,
                        home_team, away_team, match_time, status, matchday,
                        home_odds, draw_odds, away_odds, synced_at
                    ) VALUES (
                        %(fixture_id)s, %(league_code)s, %(league_name)s,
                        %(home_team)s,  %(away_team)s,  %(match_time)s,
                        %(status)s,     %(matchday)s,
                        %(home_odds)s,  %(draw_odds)s,  %(away_odds)s,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (fixture_id) DO UPDATE SET
                        status      = EXCLUDED.status,
                        home_odds   = COALESCE(EXCLUDED.home_odds, upcoming_fixtures.home_odds),
                        draw_odds   = COALESCE(EXCLUDED.draw_odds, upcoming_fixtures.draw_odds),
                        away_odds   = COALESCE(EXCLUDED.away_odds, upcoming_fixtures.away_odds),
                        synced_at   = CURRENT_TIMESTAMP,
                        updated_at  = CURRENT_TIMESTAMP
                """, f)
                saved += 1
            conn.commit()
    except Exception as e:
        logger.error(f"写入 upcoming_fixtures 失败: {e}", exc_info=True)
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# 3. 批量 ML 推理
# ─────────────────────────────────────────────────────────────────────────────

def batch_ml_predict(db) -> int:
    """对所有未开赛比赛批量计算 ML 概率并写回数据库"""
    import pickle
    from scripts.feature_engineering import load_historical_matches, compute_team_stats
    from scripts.train_model import predict_probabilities

    model_path = os.path.join(
        os.path.dirname(__file__), '..', 'models', 'match_predictor_all.pkl'
    )
    if not os.path.exists(model_path):
        logger.warning("⚠️  ML 模型不存在，跳过批量预测（请先运行 train_model.py）")
        return 0

    logger.info("📊 加载历史数据和模型用于批量预测...")
    with open(model_path, 'rb') as f:
        model_pkg = pickle.load(f)

    df = load_historical_matches(db)
    if df.empty:
        logger.warning("历史数据为空，跳过批量预测")
        return 0

    team_stats = compute_team_stats(df, lookback=10)
    updated = 0

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, home_team, away_team, home_odds, draw_odds, away_odds
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED', 'TIMED')
                  AND match_time > NOW()
            """)
            rows = cur.fetchall()
            logger.info(f"共 {len(rows)} 场待预测比赛")

            for fixture_id, home_team, away_team, home_odds, draw_odds, away_odds in rows:
                proba = predict_probabilities(
                    home_team, away_team, team_stats, df, model_pkg
                )
                if not proba:
                    continue

                ph = proba.get('H', 0.0)
                pd_ = proba.get('D', 0.0)
                pa  = proba.get('A', 0.0)

                # Kelly 推荐
                recommendation = _kelly_recommend(
                    proba, home_odds, draw_odds, away_odds
                )

                cur.execute("""
                    UPDATE upcoming_fixtures
                    SET ml_home_prob = %s,
                        ml_draw_prob = %s,
                        ml_away_prob = %s,
                        ml_recommendation = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fixture_id = %s
                """, (float(ph), float(pd_), float(pa), recommendation, fixture_id))
                updated += 1

            conn.commit()
    except Exception as e:
        logger.error(f"批量预测写入失败: {e}", exc_info=True)

    logger.info(f"✅ 完成批量 ML 预测，更新 {updated} 场")
    return updated


def _kelly_recommend(proba: dict, home_odds: Optional[float],
                     draw_odds: Optional[float], away_odds: Optional[float]) -> str:
    label_map = {'H': '主胜', 'D': '平局', 'A': '客胜'}
    if not proba:
        return "数据不足"
    best = max(proba, key=proba.get)
    best_prob = proba[best]

    if home_odds and draw_odds and away_odds:
        total = 1/home_odds + 1/draw_odds + 1/away_odds
        impl = {'H': 1/home_odds/total, 'D': 1/draw_odds/total, 'A': 1/away_odds/total}
        edge = best_prob - impl.get(best, 0)
        if edge >= 0.07:
            return f"强推{label_map[best]}（优势{edge*100:.1f}%）"
        elif edge >= 0.04:
            return f"偏向{label_map[best]}（优势{edge*100:.1f}%）"
        else:
            return f"谨慎/{label_map[best]}（优势<4%）"
    else:
        return f"ML倾向{label_map[best]}（{best_prob*100:.1f}%）"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 赔率同步到 match_odds 表（开盘追踪）
# ─────────────────────────────────────────────────────────────────────────────

def sync_odds_movement(fixtures: list, db) -> int:
    """
    将赔率同步到 match_odds 表。
    首次写入时同时设置 open_*_odds（开盘赔率），后续更新只改 home/draw/away_odds。
    """
    synced = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for f in fixtures:
                if f['home_odds'] is None:
                    continue
                match_date = f['match_time'].date() if f['match_time'] else None
                if not match_date:
                    continue

                cur.execute("""
                    INSERT INTO match_odds (
                        match_id, home_team, away_team, match_date, bookmaker,
                        home_odds, draw_odds, away_odds,
                        open_home_odds, open_draw_odds, open_away_odds,
                        odds_source
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (home_team, away_team, match_date, bookmaker) DO UPDATE SET
                        home_odds = EXCLUDED.home_odds,
                        draw_odds = EXCLUDED.draw_odds,
                        away_odds = EXCLUDED.away_odds,
                        -- 开盘赔率只在首次写入时设置
                        open_home_odds = COALESCE(match_odds.open_home_odds, EXCLUDED.home_odds),
                        open_draw_odds = COALESCE(match_odds.open_draw_odds, EXCLUDED.draw_odds),
                        open_away_odds = COALESCE(match_odds.open_away_odds, EXCLUDED.away_odds),
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    f['fixture_id'],
                    f['home_team'], f['away_team'],
                    match_date, 'football-data',
                    f['home_odds'], f['draw_odds'], f['away_odds'],
                    f['home_odds'], f['draw_odds'], f['away_odds'],
                    'football-data',
                ))
                synced += 1
            conn.commit()
    except Exception as e:
        logger.error(f"同步赔率变动失败: {e}", exc_info=True)
    return synced


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="同步未开赛赛程 + ML 批量预测")
    parser.add_argument("--leagues", default=",".join(DEFAULT_LEAGUES))
    parser.add_argument("--days",    type=int, default=14, help="未来N天")
    parser.add_argument("--no-ml",   action="store_true", help="跳过 ML 预测步骤")
    args = parser.parse_args()

    from scripts.database import prediction_db as db

    leagues = [l.strip() for l in args.leagues.split(",")]

    print("=" * 60)
    print("🔄 同步未开赛赛程 + 赔率 + ML 预测")
    print(f"   联赛: {', '.join(leagues)}  |  未来 {args.days} 天")
    print("=" * 60)

    all_fixtures = []

    for league_id in leagues:
        logger.info(f"📥 {LEAGUE_NAMES.get(league_id, league_id)} ({league_id})")
        raw = fetch_upcoming(league_id, args.days)
        if not raw:
            time.sleep(7)
            continue

        fixtures = [parse_fixture(m, league_id) for m in raw]
        saved = upsert_fixtures(fixtures, db)
        odds_synced = sync_odds_movement(fixtures, db)
        all_fixtures.extend(fixtures)

        logger.info(f"   ✅ {saved} 场赛程  |  {odds_synced} 条赔率")
        time.sleep(7)  # 免费版限速

    print(f"\n📊 共同步 {len(all_fixtures)} 场未开赛比赛")

    # 批量 ML 预测
    if not args.no_ml:
        print("\n🤖 批量 ML 预测...")
        updated = batch_ml_predict(db)
        print(f"   ✅ {updated} 场比赛已更新 ML 预测概率")

    # 打印预览
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT league_name, home_team, away_team, match_time,
                       ml_home_prob, ml_draw_prob, ml_away_prob, ml_recommendation
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED', 'TIMED') AND match_time > NOW()
                ORDER BY match_time ASC
                LIMIT 10
            """)
            rows = cur.fetchall()
        print("\n📋 即将开赛比赛（前10场）:")
        for r in rows:
            league, ht, at, mt, ph, pd_, pa, rec = r
            t = mt.strftime("%m-%d %H:%M") if mt else "??"
            prob_str = f"主{ph*100:.0f}%/平{pd_*100:.0f}%/客{pa*100:.0f}%" if ph else "待预测"
            print(f"  [{league}] {t}  {ht} vs {at}")
            print(f"          {prob_str}  →  {rec or '暂无推荐'}")
    except Exception as e:
        logger.error(f"打印预览失败: {e}")

    print("\n🎉 同步完成！")


if __name__ == "__main__":
    main()
