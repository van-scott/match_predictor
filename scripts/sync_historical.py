#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史比赛数据同步脚本
从 football-data.org 拉取五大联赛近3个赛季数据，写入 historical_matches 表
用法: python scripts/sync_historical.py [--leagues PL,PD,SA,BL1,FL1] [--seasons 2022,2023,2024]
"""
import os
import sys
import time
import requests
import argparse
from datetime import datetime

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.database import prediction_db

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "d318f21f939e4752a93313937fd203e9")
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

LEAGUE_NAMES = {
    "PL":  "英超",
    "PD":  "西甲",
    "SA":  "意甲",
    "BL1": "德甲",
    "FL1": "法甲",
    "CL":  "欧冠",
}

RESULT_MAP = {
    "HOME_TEAM": "H",
    "AWAY_TEAM": "A",
    "DRAW":      "D",
}


def fetch_season_matches(league_id: str, season: int) -> list:
    """拉取某赛季某联赛的所有比赛"""
    url = f"{BASE_URL}/competitions/{league_id}/matches"
    params = {"season": season}
    
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if resp.status_code == 200:
            matches = resp.json().get("matches", [])
            print(f"  ✅ {league_id} {season}/{season+1} — 获取到 {len(matches)} 场")
            return matches
        elif resp.status_code == 403:
            print(f"  ❌ {league_id} {season} — API权限不足（免费版不支持此联赛/赛季）")
            return []
        elif resp.status_code == 429:
            print(f"  ⏳ 触发限速，等待 60 秒...")
            time.sleep(60)
            return fetch_season_matches(league_id, season)
        else:
            print(f"  ⚠️ {league_id} {season} — HTTP {resp.status_code}: {resp.text[:200]}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"  ❌ 网络错误: {e}")
        return []


def parse_match(raw: dict, league_id: str, season: int) -> dict:
    """将 API 原始数据解析为 historical_matches 的插入格式"""
    score = raw.get("score", {})
    ft = score.get("fullTime", {})
    ht = score.get("halfTime", {})
    winner = score.get("winner")

    # 解析时间
    utc_date = raw.get("utcDate", "")
    match_dt = None
    match_date = None
    match_time = None
    if utc_date:
        try:
            match_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            match_date = match_dt.date()
            match_time = match_dt.time()
        except Exception:
            pass

    return {
        "match_id":             str(raw.get("id", "")),
        "season":               f"{season}/{season+1}",
        "league_name":          LEAGUE_NAMES.get(league_id, league_id),
        "match_date":           match_date,
        "match_time":           match_time,
        "match_datetime":       match_dt,
        "home_team":            raw.get("homeTeam", {}).get("name", ""),
        "away_team":            raw.get("awayTeam", {}).get("name", ""),
        "home_score":           ft.get("home"),
        "away_score":           ft.get("away"),
        "result":               RESULT_MAP.get(winner),
        "half_time_home":       ht.get("home"),
        "half_time_away":       ht.get("away"),
    }


def sync_to_db(matches_list: list) -> int:
    """批量写入数据库，返回成功数"""
    if not matches_list:
        return 0

    # 只同步已完赛的比赛
    finished = [m for m in matches_list if m.get("result") is not None]
    
    saved = prediction_db.save_historical_matches(finished)
    return saved


def main():
    parser = argparse.ArgumentParser(description="同步 football-data.org 历史比赛数据")
    parser.add_argument("--leagues",  default="PL,PD,SA,BL1,FL1",
                        help="联赛代码，逗号分隔 (默认: PL,PD,SA,BL1,FL1)")
    parser.add_argument("--seasons",  default="2022,2023,2024",
                        help="赛季起始年，逗号分隔 (默认: 2022,2023,2024)")
    args = parser.parse_args()

    leagues = [l.strip() for l in args.leagues.split(",")]
    seasons = [int(s.strip()) for s in args.seasons.split(",")]

    print("=" * 60)
    print(f"🚀 开始同步历史比赛数据")
    print(f"   联赛: {', '.join(leagues)}")
    print(f"   赛季: {', '.join(str(s) for s in seasons)}")
    print("=" * 60)

    total_saved = 0

    for league_id in leagues:
        for season in seasons:
            print(f"\n📥 {LEAGUE_NAMES.get(league_id, league_id)} ({league_id}) {season}/{season+1}赛季")

            raw_matches = fetch_season_matches(league_id, season)
            if not raw_matches:
                continue

            parsed = [parse_match(m, league_id, season) for m in raw_matches]
            saved = sync_to_db(parsed)
            total_saved += saved

            print(f"  💾 本批入库: {saved} 场")

            # 免费版 API 限速: 10次/分钟，每次请求后等待 7 秒
            time.sleep(7)

    print("\n" + "=" * 60)
    print(f"🎉 同步完成！共入库 {total_saved} 场历史比赛")
    print("=" * 60)

    # 打印库内统计
    try:
        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT league_name, COUNT(*) FROM historical_matches GROUP BY league_name ORDER BY COUNT(*) DESC")
            rows = cur.fetchall()
        print("\n📊 当前数据库各联赛比赛数量:")
        for league, cnt in rows:
            print(f"   {league}: {cnt} 场")
    except Exception as e:
        print(f"统计查询失败: {e}")


if __name__ == "__main__":
    main()
