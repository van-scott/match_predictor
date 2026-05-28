# -*- coding: utf-8 -*-
"""
流水线各步骤
──────────────
每个步骤是独立函数，接受 db 和配置参数，返回标准化结果字典：
  {
    "processed": int,   # 实际处理的比赛数
    "skipped":   int,   # 跳过（无数据/已处理）
    "errors":    int,   # 出错数
    "detail":    list,  # 关键日志行（供 runner 汇总打印）
  }

每个步骤都可以单独调用和测试。
"""
from __future__ import annotations

import math
import os
import time
import logging
import unicodedata
import re
from datetime import datetime, timezone, timedelta
from difflib import get_close_matches
from decimal import Decimal
from typing import Optional

import requests

from matchpredict.pipeline.config import (
    FOOTBALL_DATA_API_KEY, FOOTBALL_DATA_BASE_URL,
    ODDS_API_KEY, ODDS_API_BASE_URL, ODDS_SPORT_MAP,
    LEAGUE_NAMES, MODEL_PATH,
)

logger = logging.getLogger("matchpredict.pipeline")

# ── 标准结果字典工厂 ──────────────────────────────────────────────────────────

def _result(processed=0, skipped=0, errors=0, detail=None):
    return {"processed": processed, "skipped": skipped, "errors": errors, "detail": detail or []}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _norm_team(name: str) -> str:
    """规范化球队名：去音调、去常见前后缀、小写，用于跨数据源模糊匹配。"""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'^(CA|CD|SC|CR|SE|EC|CS|CDP|CAR|CF|RC|FC|AC|AS|SS|OGC|UD|RCD|SV|VfB|VfL|1\.|AFC|RB|SL)\s+', '', s.strip(), flags=re.I)
    s = re.sub(r'\s+(FC|SC|AC|BC|CF|de\s+\w+|FBPA|\d{4}|-[A-Z]{2,})$', '', s.strip(), flags=re.I)
    s = re.sub(r'[-_].*$', '', s)
    return s.strip().lower()


def _normalize_float(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _odds_valid(ho, do_, ao) -> bool:
    """赔率三项都存在且大于 1.01 才视为有效。"""
    try:
        return all(float(x) > 1.01 for x in (ho, do_, ao))
    except (TypeError, ValueError):
        return False


def _odds_to_prob(ho, do_, ao) -> Optional[tuple[float, float, float]]:
    """赔率 → 归一化隐含概率，无效时返回 None。"""
    if not _odds_valid(ho, do_, ao):
        return None
    rh, rd, ra = 1 / float(ho), 1 / float(do_), 1 / float(ao)
    total = rh + rd + ra
    return rh / total, rd / total, ra / total


def _poisson_score(ph: float, pa: float) -> tuple[int, int]:
    """主胜/客胜概率 → 泊松最可能比分（限 0-5）。"""
    lam_h = max(-math.log(max(1 - ph, 0.01)) * 1.5, 0.3)
    lam_a = max(-math.log(max(1 - pa, 0.01)) * 1.5, 0.3)
    return min(round(lam_h), 5), min(round(lam_a), 5)


def _kelly_label(proba: dict, ho, do_, ao) -> str:
    """根据 ML 概率 + 赔率计算 Kelly 推荐标签。"""
    label_map = {"H": "主胜", "D": "平局", "A": "客胜"}
    prob_only = {k: v for k, v in proba.items() if k in ("H", "D", "A")}
    if not prob_only:
        return "数据不足"
    best = max(prob_only, key=prob_only.get)
    best_prob = float(prob_only[best])
    confidence = proba.get("confidence", "medium")
    if confidence == "low":
        return f"低置信度/{label_map[best]}（{best_prob*100:.1f}%，不推荐）"
    if _odds_valid(ho, do_, ao):
        ho_f, do_f, ao_f = float(ho), float(do_), float(ao)
        total = 1/ho_f + 1/do_f + 1/ao_f
        impl = {"H": 1/ho_f/total, "D": 1/do_f/total, "A": 1/ao_f/total}
        edge = best_prob - impl.get(best, 0)
        tag = "⭐" if confidence == "high" else ""
        if edge >= 0.07:
            return f"强推{label_map[best]}（优势{edge*100:.1f}%）{tag}"
        elif edge >= 0.04:
            return f"偏向{label_map[best]}（优势{edge*100:.1f}%）{tag}"
        return f"谨慎/{label_map[best]}（优势<4%）"
    return (f"ML强推{label_map[best]}（{best_prob*100:.1f}%）⭐" if confidence == "high"
            else f"ML倾向{label_map[best]}（{best_prob*100:.1f}%）")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — 拉取赛程（football-data + 竞彩让球盘）
# ─────────────────────────────────────────────────────────────────────────────

def step_fetch_fixtures(db, leagues: list[str], days_ahead: int) -> dict:
    """
    从 football-data.org 拉取未开赛比赛，UPSERT 到 upcoming_fixtures。
    同时从竞彩（ChinaLotterySpider）拉取让一球赔率的比赛补充入库。
    赔率写入 upcoming_fixtures + match_odds（开盘追踪）。
    """
    logger.info("━━━ Step 1: 拉取赛程 (football-data, 窗口=%d天) ━━━", days_ahead)
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    now = datetime.now(timezone.utc)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    total_fixtures = 0
    total_saved = 0
    errors = 0
    detail = []

    try:
        from psycopg2.extras import execute_batch
    except ImportError:
        execute_batch = None  # fallback：逐条写

    for league_id in leagues:
        league_name = LEAGUE_NAMES.get(league_id, league_id)
        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{league_id}/matches"
        params = {"status": "SCHEDULED,TIMED", "dateFrom": date_from, "dateTo": date_to}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 429:
                logger.warning("  [%s] 限速，等待 60s ...", league_id)
                time.sleep(60)
                resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code in (403, 404):
                logger.debug("  [%s] 免费套餐不含该联赛，跳过", league_id)
                time.sleep(7)
                continue
            if resp.status_code != 200:
                logger.warning("  [%s] HTTP %s，跳过", league_id, resp.status_code)
                errors += 1
                time.sleep(7)
                continue

            raw_matches = resp.json().get("matches", [])
            if not raw_matches:
                logger.info("  [%s] 无未开赛比赛", league_name)
                time.sleep(7)
                continue

            # 解析
            fixtures = []
            for m in raw_matches:
                utc_date = m.get("utcDate", "")
                match_dt = None
                if utc_date:
                    try:
                        match_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                    except Exception:
                        pass
                odds_raw = m.get("odds", {})
                ho = odds_raw.get("homeWin") if isinstance(odds_raw, dict) else None
                do_ = odds_raw.get("draw") if isinstance(odds_raw, dict) else None
                ao = odds_raw.get("awayWin") if isinstance(odds_raw, dict) else None
                fixtures.append({
                    "fixture_id": str(m.get("id", "")),
                    "league_code": league_id,
                    "league_name": league_name,
                    "home_team": m.get("homeTeam", {}).get("name", ""),
                    "away_team": m.get("awayTeam", {}).get("name", ""),
                    "match_time": match_dt,
                    "status": m.get("status", "TIMED"),
                    "matchday": m.get("matchday"),
                    "home_odds": ho,
                    "draw_odds": do_,
                    "away_odds": ao,
                })
            total_fixtures += len(fixtures)

            # 写库
            saved = _upsert_fixtures(fixtures, db)
            _upsert_match_odds(fixtures, db)  # 开盘追踪
            total_saved += saved
            msg = f"  [{league_name}] {len(fixtures)} 场赛程，已写 {saved} 条"
            logger.info(msg)
            detail.append(msg)

        except Exception as e:
            logger.error("  [%s] 拉取异常: %s", league_id, e, exc_info=True)
            errors += 1

        time.sleep(7)  # football-data 免费限速

    # 竞彩让一球补充（无 football-data 数据的联赛也能进入赛事广场）
    lottery_added = _sync_lottery_hhad(days_ahead, db)
    if lottery_added:
        msg = f"  [竞彩] 让一球赔率补充 {lottery_added} 场"
        logger.info(msg)
        detail.append(msg)

    logger.info("  Step 1 完成: API=%d场 / 写库=%d / 竞彩补充=%d / 错误=%d",
                total_fixtures, total_saved, lottery_added, errors)
    return _result(processed=total_saved + lottery_added, errors=errors, detail=detail)


def _upsert_fixtures(fixtures: list, db) -> int:
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
                        %(home_team)s, %(away_team)s, %(match_time)s,
                        %(status)s, %(matchday)s,
                        %(home_odds)s, %(draw_odds)s, %(away_odds)s,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (fixture_id) DO UPDATE SET
                        status    = CASE WHEN upcoming_fixtures.status = 'FINISHED'
                                         THEN upcoming_fixtures.status
                                         ELSE EXCLUDED.status END,
                        home_odds = COALESCE(EXCLUDED.home_odds, upcoming_fixtures.home_odds),
                        draw_odds = COALESCE(EXCLUDED.draw_odds, upcoming_fixtures.draw_odds),
                        away_odds = COALESCE(EXCLUDED.away_odds, upcoming_fixtures.away_odds),
                        synced_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                """, f)
                saved += 1
            conn.commit()
    except Exception as e:
        logger.error("写入 upcoming_fixtures 失败: %s", e, exc_info=True)
    return saved


def _upsert_match_odds(fixtures: list, db):
    """开盘赔率追踪：首次写入时保存 open_*，后续只更新当前赔率。"""
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for f in fixtures:
                if f["home_odds"] is None:
                    continue
                match_date = f["match_time"].date() if f["match_time"] else None
                if not match_date:
                    continue
                cur.execute("""
                    INSERT INTO match_odds (
                        match_id, home_team, away_team, match_date, bookmaker,
                        home_odds, draw_odds, away_odds,
                        open_home_odds, open_draw_odds, open_away_odds, odds_source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (home_team, away_team, match_date, bookmaker) DO UPDATE SET
                        home_odds      = EXCLUDED.home_odds,
                        draw_odds      = EXCLUDED.draw_odds,
                        away_odds      = EXCLUDED.away_odds,
                        open_home_odds = COALESCE(match_odds.open_home_odds, EXCLUDED.home_odds),
                        open_draw_odds = COALESCE(match_odds.open_draw_odds, EXCLUDED.draw_odds),
                        open_away_odds = COALESCE(match_odds.open_away_odds, EXCLUDED.away_odds),
                        updated_at     = CURRENT_TIMESTAMP
                """, (
                    f["fixture_id"], f["home_team"], f["away_team"],
                    match_date, "football-data",
                    f["home_odds"], f["draw_odds"], f["away_odds"],
                    f["home_odds"], f["draw_odds"], f["away_odds"],
                    "football-data",
                ))
            conn.commit()
    except Exception as e:
        logger.error("写入 match_odds 失败: %s", e, exc_info=True)


def _sync_lottery_hhad(days_ahead: int, db) -> int:
    """竞彩让一球赔率（±1 盘口），补充到 upcoming_fixtures。"""
    try:
        from matchpredict.integrations.lottery_odds import ChinaLotterySpider
    except ImportError:
        return 0
    spider = ChinaLotterySpider()
    matches = spider.get_formatted_matches(days_ahead=days_ahead)
    if not matches:
        return 0
    count = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for m in matches:
                odds = (m.get("odds") or {}).get("hhad") or {}
                goal_line = str((m.get("odds") or {}).get("goal_line") or "").strip()
                if not (odds.get("h") and odds.get("d") and odds.get("a")):
                    continue
                if goal_line not in {"-1", "-1.0", "+1", "1", "1.0"}:
                    continue
                mt = m.get("match_time")
                if not mt:
                    continue
                try:
                    match_dt = datetime.strptime(mt, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    try:
                        match_dt = datetime.strptime(mt, "%Y-%m-%d %H:%M")
                    except Exception:
                        continue
                fixture_id = f"dm_{m.get('match_id', '')}".replace("dm_lottery_", "dm_")
                if fixture_id == "dm_":
                    continue
                ho, do_, ao = float(odds["h"]), float(odds["d"]), float(odds["a"])
                cur.execute("""
                    INSERT INTO upcoming_fixtures (
                        fixture_id, league_code, league_name,
                        home_team, away_team, match_time, status, matchday,
                        home_odds, draw_odds, away_odds, synced_at
                    ) VALUES (%s,'DM',%s,%s,%s,%s,'SCHEDULED',NULL,%s,%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT (fixture_id) DO UPDATE SET
                        home_team  = EXCLUDED.home_team,
                        away_team  = EXCLUDED.away_team,
                        match_time = EXCLUDED.match_time,
                        status     = CASE WHEN upcoming_fixtures.status = 'FINISHED'
                                         THEN upcoming_fixtures.status ELSE EXCLUDED.status END,
                        home_odds  = EXCLUDED.home_odds,
                        draw_odds  = EXCLUDED.draw_odds,
                        away_odds  = EXCLUDED.away_odds,
                        synced_at  = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                """, (fixture_id, m.get("league_name") or "竞彩",
                      m.get("home_team") or "", m.get("away_team") or "",
                      match_dt, ho, do_, ao))
                if cur.rowcount > 0:
                    count += 1
            conn.commit()
    except Exception as e:
        logger.error("同步竞彩让一球失败: %s", e, exc_info=True)
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — 同步赔率（the-odds-api）
# ─────────────────────────────────────────────────────────────────────────────

def step_sync_odds(db) -> dict:
    """
    从 the-odds-api.com 拉取最新 h2h 赔率，更新到 upcoming_fixtures。
    只有赔率确实发生变化时才写库（IS DISTINCT FROM 判断）。
    返回变化的 fixture_id 集合供下游 ML 步骤使用。
    """
    logger.info("━━━ Step 2: 同步赔率 (the-odds-api) ━━━")
    if not ODDS_API_KEY:
        logger.warning("  ODDS_API_KEY 未设置，跳过")
        return {**_result(), "changed_ids": set()}

    updated = 0
    errors = 0
    changed_ids: set[str] = set()
    detail = []

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            # 预加载所有待赔率的未开赛比赛
            cur.execute("""
                SELECT fixture_id, league_code, home_team, away_team
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW()
            """)
            db_by_league: dict[str, list] = {}
            for fid, lc, ht, at in cur.fetchall():
                db_by_league.setdefault(lc, []).append(
                    (fid, _norm_team(ht), _norm_team(at), ht, at)
                )

            for league_code, sport_key in ODDS_SPORT_MAP.items():
                if league_code not in db_by_league:
                    continue
                try:
                    resp = requests.get(
                        f"{ODDS_API_BASE_URL}/{sport_key}/odds",
                        params={"apiKey": ODDS_API_KEY, "regions": "eu",
                                "markets": "h2h", "oddsFormat": "decimal"},
                        timeout=15,
                    )
                    if resp.status_code == 404:
                        logger.debug("  [%s] 赛季外无赔率，跳过", league_code)
                        continue
                    if resp.status_code != 200:
                        logger.warning("  [%s] 赔率 API HTTP %s", league_code, resp.status_code)
                        errors += 1
                        continue

                    api_matches = resp.json()
                    if not api_matches:
                        continue

                    league_fixtures = db_by_league[league_code]
                    norm_home_idx = {row[1]: row for row in league_fixtures}
                    norm_away_idx = {row[2]: row for row in league_fixtures}

                    league_updated = 0
                    for m in api_matches:
                        api_home = m.get("home_team", "")
                        api_away = m.get("away_team", "")
                        bookmakers = m.get("bookmakers", [])
                        if not bookmakers:
                            continue
                        outcomes: dict = {}
                        best_bk = max(bookmakers, key=lambda b: len(b.get("markets", [])), default=None)
                        if best_bk:
                            for mkt in best_bk.get("markets", []):
                                if mkt.get("key") == "h2h":
                                    for o in mkt.get("outcomes", []):
                                        outcomes[o["name"]] = o["price"]
                                    break
                        h_odds = outcomes.get(api_home)
                        a_odds = outcomes.get(api_away)
                        d_odds = outcomes.get("Draw")
                        if not h_odds or not a_odds:
                            continue

                        norm_h = _norm_team(api_home)
                        norm_a = _norm_team(api_away)
                        row = norm_home_idx.get(norm_h) or norm_away_idx.get(norm_a)
                        if not row:
                            best = get_close_matches(norm_h, list(norm_home_idx), n=1, cutoff=0.6)
                            if best:
                                row = norm_home_idx[best[0]]
                        if not row:
                            logger.debug("  未匹配: %s vs %s", api_home, api_away)
                            continue

                        fixture_id = row[0]
                        orig_home, orig_away = row[3], row[4]
                        cur.execute("""
                            UPDATE upcoming_fixtures
                            SET home_odds = %s, draw_odds = %s, away_odds = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE fixture_id = %s
                              AND (home_odds IS DISTINCT FROM %s
                                OR draw_odds IS DISTINCT FROM %s
                                OR away_odds IS DISTINCT FROM %s)
                        """, (h_odds, d_odds, a_odds, fixture_id,
                              h_odds, d_odds, a_odds))
                        if cur.rowcount > 0:
                            league_updated += 1
                            changed_ids.add(fixture_id)
                            logger.info("  💰 %s vs %s: %.2f/%.2f/%.2f",
                                        orig_home, orig_away, h_odds, d_odds or 0, a_odds)
                    if league_updated:
                        msg = f"  [{LEAGUE_NAMES.get(league_code, league_code)}] 赔率更新 {league_updated} 场"
                        detail.append(msg)
                    updated += league_updated
                    time.sleep(1)
                except Exception as e:
                    logger.error("  [%s] 赔率同步异常: %s", league_code, e)
                    errors += 1
            conn.commit()
    except Exception as e:
        logger.error("赔率同步失败: %s", e, exc_info=True)
        errors += 1

    logger.info("  Step 2 完成: 赔率变更=%d / 错误=%d", updated, errors)
    return {**_result(processed=updated, errors=errors, detail=detail), "changed_ids": changed_ids}


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — ML 概率预测
# ─────────────────────────────────────────────────────────────────────────────

def step_ml_predict(db, changed_ids: Optional[set[str]] = None) -> dict:
    """
    对未开赛且有赔率的比赛运行 ML 模型，写入 ml_home_prob/ml_draw_prob/ml_away_prob。
    优先处理赔率刚变化的比赛（changed_ids），其次处理所有无概率的比赛。
    若模型文件不存在直接跳过。
    """
    logger.info("━━━ Step 3: ML 概率预测 ━━━")
    if not os.path.exists(MODEL_PATH):
        logger.warning("  ML 模型不存在 (%s)，跳过（请先运行 train_model.py）", MODEL_PATH)
        return _result(skipped=1)

    import pickle
    try:
        from matchpredict.ml.features import load_historical_matches, compute_team_stats
        from matchpredict.ml.training import predict_probabilities
    except ImportError as e:
        logger.error("  导入 ML 工具失败: %s", e)
        return _result(errors=1)

    try:
        with open(MODEL_PATH, "rb") as f:
            model_pkg = pickle.load(f)
    except Exception as e:
        logger.error("  加载模型失败: %s", e)
        return _result(errors=1)

    df = load_historical_matches(db)
    if df.empty:
        logger.warning("  历史数据为空，跳过 ML 预测")
        return _result(skipped=1)
    team_stats = compute_team_stats(df, lookback=10)

    updated = 0
    skipped = 0
    errors = 0
    detail = []

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            if changed_ids:
                # 只重预测赔率变化的比赛 + 从未预测过的比赛
                cur.execute("""
                    SELECT fixture_id, home_team, away_team, home_odds, draw_odds, away_odds
                    FROM upcoming_fixtures
                    WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW()
                      AND home_odds IS NOT NULL AND draw_odds IS NOT NULL AND away_odds IS NOT NULL
                      AND (fixture_id = ANY(%s) OR ml_home_prob IS NULL)
                """, (list(changed_ids),))
            else:
                cur.execute("""
                    SELECT fixture_id, home_team, away_team, home_odds, draw_odds, away_odds
                    FROM upcoming_fixtures
                    WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW()
                      AND home_odds IS NOT NULL AND draw_odds IS NOT NULL AND away_odds IS NOT NULL
                      AND ml_home_prob IS NULL
                """)
            rows = cur.fetchall()

            if not rows:
                logger.info("  无需 ML 预测的比赛（均已预测且赔率未变）")
                return _result(skipped=0)

            logger.info("  待 ML 预测: %d 场", len(rows))

            for fixture_id, home_team, away_team, ho, do_, ao in rows:
                try:
                    proba = predict_probabilities(
                        home_team, away_team, team_stats, df, model_pkg,
                        home_odds=_normalize_float(ho),
                        draw_odds=_normalize_float(do_),
                        away_odds=_normalize_float(ao),
                    )
                    if not proba:
                        skipped += 1
                        continue
                    ph = float(proba.get("H", 0.0))
                    pd_v = float(proba.get("D", 0.0))
                    pa = float(proba.get("A", 0.0))
                    rec = _kelly_label(proba, ho, do_, ao)
                    cur.execute("""
                        UPDATE upcoming_fixtures SET
                            ml_home_prob = %s, ml_draw_prob = %s, ml_away_prob = %s,
                            ml_recommendation = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE fixture_id = %s
                    """, (ph, pd_v, pa, rec, fixture_id))
                    logger.info("  🤖 %s vs %s → 主%.0f%%/平%.0f%%/客%.0f%%  %s",
                                home_team, away_team, ph*100, pd_v*100, pa*100, rec)
                    updated += 1
                except Exception as e:
                    logger.error("  ML 预测单场失败 [%s]: %s", fixture_id, e)
                    errors += 1

            conn.commit()
    except Exception as e:
        logger.error("ML 预测步骤失败: %s", e, exc_info=True)
        errors += 1

    msg = f"  Step 3 完成: 更新={updated} / 跳过={skipped} / 错误={errors}"
    logger.info(msg)
    detail.append(msg)
    return _result(processed=updated, skipped=skipped, errors=errors, detail=detail)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — 泊松比分预测
# ─────────────────────────────────────────────────────────────────────────────

def step_poisson_scores(db, days_back: int = 14) -> dict:
    """
    对有 ML 概率但无预测比分的比赛，用泊松反推预测比分。
    对无 ML 概率但有赔率的已过时比赛，先推概率再推比分（dm_ 来源兜底）。
    """
    logger.info("━━━ Step 4: 泊松比分预测 ━━━")
    try:
        from psycopg2.extras import execute_batch
    except ImportError:
        execute_batch = None

    score_filled = 0
    prob_from_odds = 0
    skipped_bad_odds = 0
    detail = []

    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            # case A: 有 ML 概率缺比分
            cur.execute("""
                SELECT fixture_id, ml_home_prob, ml_away_prob
                FROM upcoming_fixtures
                WHERE ml_home_prob IS NOT NULL AND predicted_home_goals IS NULL
                  AND match_time > NOW() - (%s || ' days')::interval
            """, (days_back,))
            payload_score = []
            for fid, ml_h, ml_a in cur.fetchall():
                ph, pa = _poisson_score(float(ml_h), float(ml_a))
                payload_score.append((ph, pa, fid))
            if payload_score:
                if execute_batch:
                    execute_batch(cur, """
                        UPDATE upcoming_fixtures SET
                            predicted_home_goals = %s, predicted_away_goals = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE fixture_id = %s AND predicted_home_goals IS NULL
                    """, payload_score, page_size=200)
                else:
                    for row in payload_score:
                        cur.execute("UPDATE upcoming_fixtures SET predicted_home_goals=%s, predicted_away_goals=%s WHERE fixture_id=%s AND predicted_home_goals IS NULL", row)
                score_filled = len(payload_score)
                logger.info("  📐 由 ML 概率推比分: %d 场", score_filled)

            # case B: 无 ML 概率但有赔率（dm_ 比赛兜底）
            cur.execute("""
                SELECT fixture_id, home_odds, draw_odds, away_odds
                FROM upcoming_fixtures
                WHERE ml_home_prob IS NULL
                  AND home_odds IS NOT NULL AND draw_odds IS NOT NULL AND away_odds IS NOT NULL
                  AND match_time > NOW() - (%s || ' days')::interval
            """, (days_back,))
            payload_odds = []
            for fid, ho, do_, ao in cur.fetchall():
                probs = _odds_to_prob(ho, do_, ao)
                if probs is None:
                    skipped_bad_odds += 1
                    continue
                ph_p, pd_p, pa_p = probs
                pred_h, pred_a = _poisson_score(ph_p, pa_p)
                payload_odds.append((ph_p, pd_p, pa_p, pred_h, pred_a, fid))
            if payload_odds:
                if execute_batch:
                    execute_batch(cur, """
                        UPDATE upcoming_fixtures SET
                            ml_home_prob = %s, ml_draw_prob = %s, ml_away_prob = %s,
                            predicted_home_goals = COALESCE(predicted_home_goals, %s),
                            predicted_away_goals = COALESCE(predicted_away_goals, %s),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE fixture_id = %s AND ml_home_prob IS NULL
                    """, payload_odds, page_size=200)
                else:
                    for row in payload_odds:
                        cur.execute("UPDATE upcoming_fixtures SET ml_home_prob=%s,ml_draw_prob=%s,ml_away_prob=%s,predicted_home_goals=COALESCE(predicted_home_goals,%s),predicted_away_goals=COALESCE(predicted_away_goals,%s) WHERE fixture_id=%s AND ml_home_prob IS NULL", row)
                prob_from_odds = len(payload_odds)
                logger.info("  📐 由赔率推概率+比分: %d 场 (跳过异常赔率 %d)", prob_from_odds, skipped_bad_odds)

            conn.commit()
    except Exception as e:
        logger.error("泊松比分预测失败: %s", e, exc_info=True)
        return _result(errors=1)

    msg = f"  Step 4 完成: 比分补全={score_filled}, 赔率推概率={prob_from_odds}, 跳过异常={skipped_bad_odds}"
    logger.info(msg)
    detail.append(msg)
    return _result(processed=score_filled + prob_from_odds,
                   skipped=skipped_bad_odds, detail=detail)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — 回填结果 & 更新准确率
# ─────────────────────────────────────────────────────────────────────────────

def step_backfill_results(db, leagues: list[str], days_back: int,
                          include_cancelled: bool = False) -> dict:
    """
    从 football-data 拉取已完赛比赛，写回实际比分、计算命中率。
    同步 dm_ 来源（OpenLigaDB）。
    最后兜底补全 ml_predicted_result 缺失的旧记录。
    """
    logger.info("━━━ Step 5: 回填比赛结果 (窗口=%d天) ━━━", days_back)
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    total_updated = 0
    total_inserted = 0
    total_correct = 0
    total_checked = 0
    total_score_hit = 0
    errors = 0
    detail = []

    status_filters = ["FINISHED"] + (["CANCELLED", "POSTPONED"] if include_cancelled else [])

    for league_id in leagues:
        league_name = LEAGUE_NAMES.get(league_id, league_id)
        all_raw: list = []
        for sf in status_filters:
            params = {
                "status": sf,
                "dateFrom": start.strftime("%Y-%m-%d"),
                "dateTo": now.strftime("%Y-%m-%d"),
            }
            try:
                resp = requests.get(
                    f"{FOOTBALL_DATA_BASE_URL}/competitions/{league_id}/matches",
                    headers=headers, params=params, timeout=15,
                )
                if resp.status_code == 429:
                    logger.warning("  [%s] 限速，等待60s ...", league_id)
                    time.sleep(60)
                    resp = requests.get(
                        f"{FOOTBALL_DATA_BASE_URL}/competitions/{league_id}/matches",
                        headers=headers, params=params, timeout=15,
                    )
                if resp.status_code == 200:
                    all_raw.extend(resp.json().get("matches", []))
                elif resp.status_code not in (403, 404):
                    logger.warning("  [%s] HTTP %s", league_id, resp.status_code)
                    errors += 1
            except Exception as e:
                logger.error("  [%s] 拉取失败: %s", league_id, e)
                errors += 1

        if not all_raw:
            logger.info("  [%s] 无已结束比赛", league_name)
            time.sleep(6)
            continue

        logger.info("  [%s] 获取 %d 场已结束比赛", league_name, len(all_raw))

        # 取消/延期状态更新
        if include_cancelled:
            _update_cancelled(all_raw, db)

        # 回填比分 + 命中率
        s = _backfill_to_db(all_raw, league_id, db)
        total_updated += s["updated"]
        total_inserted += s["inserted"]
        total_correct += s["correct"]
        total_checked += s["total_checked"]
        total_score_hit += s["score_hit"]

        if s["updated"] or s["inserted"]:
            msg = (f"  [{league_name}] 更新={s['updated']} 新增={s['inserted']}"
                   + (f" 命中={s['correct']}/{s['total_checked']}" if s['total_checked'] else ""))
            logger.info(msg)
            detail.append(msg)

        time.sleep(6)

    # dm_ 彩票来源结果同步
    dm_updated = _sync_dm_results(db)
    if dm_updated:
        msg = f"  [竞彩dm_] 结果同步 {dm_updated} 场"
        logger.info(msg)
        detail.append(msg)

    # 兜底：历史遗留的 ml_predicted_result 缺失
    backfilled = _backfill_ml_predicted_result(db, days_back=max(days_back * 2, 30))
    if backfilled:
        logger.info("  🔧 兜底补全 ml_predicted_result: %d 场", backfilled)

    summary = (f"  Step 5 完成: 更新={total_updated} 新增={total_inserted}"
               + (f" 命中率={total_correct}/{total_checked}={total_correct/total_checked*100:.1f}%"
                  if total_checked else ""))
    logger.info(summary)
    detail.append(summary)
    return _result(processed=total_updated + total_inserted, errors=errors, detail=detail)


def _update_cancelled(matches: list, db):
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for m in matches:
                st = m.get("status", "")
                if st not in ("CANCELLED", "POSTPONED"):
                    continue
                cur.execute("""
                    UPDATE upcoming_fixtures SET status=%s, updated_at=CURRENT_TIMESTAMP
                    WHERE fixture_id=%s AND status NOT IN ('FINISHED')
                """, (st, str(m.get("id", ""))))
            conn.commit()
    except Exception as e:
        logger.error("更新取消/延期状态失败: %s", e)


def _backfill_to_db(matches: list, league_code: str, db) -> dict:
    stats = {"updated": 0, "inserted": 0, "correct": 0, "score_hit": 0, "total_checked": 0}
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for m in matches:
                if m.get("status") in ("CANCELLED", "POSTPONED"):
                    continue
                fixture_id = str(m.get("id", ""))
                ft = m.get("score", {}).get("fullTime", {})
                home_goals = ft.get("home")
                away_goals = ft.get("away")
                if home_goals is None or away_goals is None:
                    continue

                actual_result = "H" if home_goals > away_goals else ("D" if home_goals == away_goals else "A")
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

                cur.execute("""
                    SELECT ml_home_prob, ml_draw_prob, ml_away_prob,
                           predicted_home_goals, predicted_away_goals
                    FROM upcoming_fixtures WHERE fixture_id = %s
                """, (fixture_id,))
                row = cur.fetchone()

                result_correct = None
                score_correct_val = None
                goal_diff_error = None
                ml_predicted_result = None
                pred_hg = None
                pred_ag = None

                if row and row[0] is not None:
                    ml_h, ml_d, ml_a = float(row[0]), float(row[1]), float(row[2])
                    pred_hg = row[3]
                    pred_ag = row[4]
                    if pred_hg is None or pred_ag is None:
                        pred_hg, pred_ag = _poisson_score(ml_h, ml_a)
                    ml_predicted_result = ("H" if ml_h >= ml_d and ml_h >= ml_a
                                           else ("D" if ml_d >= ml_a else "A"))
                    result_correct = (ml_predicted_result == actual_result)
                    stats["total_checked"] += 1
                    if result_correct:
                        stats["correct"] += 1
                    if pred_hg is not None and pred_ag is not None:
                        score_correct_val = (pred_hg == home_goals and pred_ag == away_goals)
                        if score_correct_val:
                            stats["score_hit"] += 1
                        goal_diff_error = abs((pred_hg - pred_ag) - (home_goals - away_goals))

                if row is not None:
                    cur.execute("""
                        UPDATE upcoming_fixtures SET
                            status='FINISHED', actual_home_goals=%s, actual_away_goals=%s,
                            actual_result=%s, finished_at=CURRENT_TIMESTAMP,
                            ml_predicted_result=%s,
                            predicted_home_goals=COALESCE(predicted_home_goals,%s),
                            predicted_away_goals=COALESCE(predicted_away_goals,%s),
                            result_correct=%s, score_correct=%s, goal_diff_error=%s,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE fixture_id=%s
                    """, (home_goals, away_goals, actual_result, ml_predicted_result,
                          pred_hg, pred_ag, result_correct, score_correct_val,
                          goal_diff_error, fixture_id))
                    if cur.rowcount > 0:
                        stats["updated"] += 1
                        icon = "✅" if result_correct else ("❌" if result_correct is False else "⚪")
                        logger.info("  %s %s %d-%d %s  ML=%s 实际=%s",
                                    icon, home_team, home_goals, away_goals, away_team,
                                    ml_predicted_result or "无", actual_result)
                else:
                    cur.execute("""
                        INSERT INTO upcoming_fixtures (
                            fixture_id, league_code, league_name,
                            home_team, away_team, match_time, matchday,
                            status, actual_home_goals, actual_away_goals, actual_result,
                            finished_at, synced_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,'FINISHED',%s,%s,%s,
                                  CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                        ON CONFLICT (fixture_id) DO NOTHING
                    """, (fixture_id, league_code, LEAGUE_NAMES.get(league_code, league_code),
                          home_team, away_team, match_time, matchday,
                          home_goals, away_goals, actual_result))
                    if cur.rowcount > 0:
                        stats["inserted"] += 1
                        logger.info("  🆕 %s %d-%d %s (新插入，无ML预测)",
                                    home_team, home_goals, away_goals, away_team)
            conn.commit()
    except Exception as e:
        logger.error("回填结果失败: %s", e, exc_info=True)
    return stats


def _sync_dm_results(db) -> int:
    """同步 dm_ 来源（竞彩/OpenLigaDB）的比赛结果。"""
    updated = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
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

            ol_results: dict = {}
            for shortcut in ("bl1", "bl2", "bl3", "rel"):
                try:
                    resp = requests.get(
                        f"https://api.openligadb.de/getmatchdata/{shortcut}/2025",
                        timeout=10,
                    )
                    if resp.status_code != 200:
                        continue
                    for m in resp.json():
                        if not m.get("matchIsFinished"):
                            continue
                        t1 = m.get("team1", {}).get("teamName", "").lower()
                        t2 = m.get("team2", {}).get("teamName", "").lower()
                        results = m.get("matchResults", [])
                        final = next((r for r in results if r.get("resultTypeID") == 2), None)
                        if final:
                            ol_results[(t1, t2)] = (final["pointsTeam1"], final["pointsTeam2"])
                except Exception:
                    pass

            def _norm(name: str) -> str:
                s = unicodedata.normalize("NFKD", name)
                s = "".join(c for c in s if not unicodedata.combining(c))
                s = re.sub(r'^(FC|SC|VfB|VfL|SpVgg|SSV|SV|TSV|RB|1\.|Rot.Weiss)\s+', '', s, flags=re.I)
                s = re.sub(r'\s+(FC|SC|e\.V\.)$', '', s, flags=re.I)
                return s.strip().lower()

            for fid, lc, home, away, match_time in pending:
                hn, an = _norm(home), _norm(away)
                score = None
                for (t1, t2), (s1, s2) in ol_results.items():
                    if (hn in t1 or t1 in hn) and (an in t2 or t2 in an):
                        score = (s1, s2)
                        break
                    if (an in t1 or t1 in an) and (hn in t2 or t2 in hn):
                        score = (s2, s1)
                        break
                if score is None:
                    continue
                hg, ag = score
                ar = "H" if hg > ag else ("D" if hg == ag else "A")
                cur.execute("""
                    UPDATE upcoming_fixtures SET
                        status='FINISHED', actual_home_goals=%s, actual_away_goals=%s,
                        actual_result=%s, finished_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE fixture_id=%s
                """, (hg, ag, ar, fid))
                if cur.rowcount > 0:
                    logger.info("  ✅ dm %s %d-%d %s", home, hg, ag, away)
                    updated += 1
            conn.commit()
    except Exception as e:
        logger.error("同步 dm_ 结果失败: %s", e, exc_info=True)
    return updated


def _backfill_ml_predicted_result(db, days_back: int = 30) -> int:
    """兜底：有实际结果 + 有 ML 概率但缺 ml_predicted_result 的旧记录。"""
    try:
        from psycopg2.extras import execute_batch
    except ImportError:
        execute_batch = None

    fixed = 0
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
                  AND finished_at > NOW() - (%s || ' days')::interval
            """, (days_back,))
            payload = []
            for fid, mlh, mld, mla, phg, pag, ahg, aag, ar in cur.fetchall():
                mlh, mld, mla = float(mlh), float(mld), float(mla)
                ml_pred = ("H" if mlh >= mld and mlh >= mla
                           else ("D" if mld >= mla else "A"))
                if phg is None:
                    phg, _ = _poisson_score(mlh, mla)
                if pag is None:
                    _, pag = _poisson_score(mlh, mla)
                rc = (ml_pred == ar)
                sc = (phg == int(ahg) and pag == int(aag)) if ahg is not None else None
                gde = abs((phg - pag) - (int(ahg) - int(aag))) if ahg is not None else None
                payload.append((ml_pred, rc, sc, gde, phg, pag, fid))
            if payload:
                if execute_batch:
                    execute_batch(cur, """
                        UPDATE upcoming_fixtures SET
                            ml_predicted_result=%s, result_correct=%s,
                            score_correct=%s, goal_diff_error=%s,
                            predicted_home_goals=COALESCE(predicted_home_goals,%s),
                            predicted_away_goals=COALESCE(predicted_away_goals,%s),
                            updated_at=CURRENT_TIMESTAMP
                        WHERE fixture_id=%s
                    """, payload, page_size=200)
                else:
                    for row in payload:
                        cur.execute("UPDATE upcoming_fixtures SET ml_predicted_result=%s,result_correct=%s,score_correct=%s,goal_diff_error=%s,predicted_home_goals=COALESCE(predicted_home_goals,%s),predicted_away_goals=COALESCE(predicted_away_goals,%s) WHERE fixture_id=%s", row)
                fixed = len(payload)
            conn.commit()
    except Exception as e:
        logger.error("兜底补全 ml_predicted_result 失败: %s", e, exc_info=True)
    return fixed
