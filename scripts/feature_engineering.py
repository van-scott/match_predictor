#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库驱动的特征工程模块
从 historical_matches 表拉取数据，计算球队特征，存入 match_features 表
"""
import os
import sys
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 从数据库加载历史比赛
# ─────────────────────────────────────────────────────────────────────────────

def load_historical_matches(db=None) -> pd.DataFrame:
    """从 historical_matches 表加载所有已完赛比赛"""
    if db is None:
        from scripts.database import prediction_db
        db = prediction_db

    try:
        with db.get_db_connection() as conn:
            df = pd.read_sql("""
                SELECT match_id, season, league_name,
                       match_datetime, match_date,
                       home_team, away_team,
                       full_time_home_goals AS home_score,
                       full_time_away_goals AS away_score,
                       full_time_result     AS result,
                       half_time_home_goals AS ht_home,
                       half_time_away_goals AS ht_away
                FROM historical_matches
                WHERE full_time_result IS NOT NULL
                  AND full_time_result IN ('H', 'D', 'A')
                  AND home_team != ''
                  AND away_team != ''
                ORDER BY match_datetime ASC
            """, conn)
        logger.info(f"✅ 已加载 {len(df)} 场历史比赛用于特征工程")
        return df
    except Exception as e:
        logger.error(f"加载历史比赛失败: {e}", exc_info=True)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 球队特征计算
# ─────────────────────────────────────────────────────────────────────────────

def compute_team_stats(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    计算每支球队的近期统计特征。
    
    返回: {team_name: {feature_name: value, ...}, ...}
    特征维度:
      home_win_rate, home_draw_rate, home_loss_rate
      away_win_rate, away_draw_rate, away_loss_rate
      home_goals_scored_avg, home_goals_conceded_avg
      away_goals_scored_avg, away_goals_conceded_avg
      overall_win_rate, recent_form (最近5场积分比率)
      goal_diff_avg (近10场净球)
    """
    all_teams = sorted(set(df['home_team'].unique()) | set(df['away_team'].unique()))
    stats = {}

    for team in all_teams:
        home_m = df[df['home_team'] == team].sort_values('match_datetime').tail(lookback)
        away_m = df[df['away_team'] == team].sort_values('match_datetime').tail(lookback)

        # ── 主场特征 ──────────────────────────────────────────────────
        if not home_m.empty:
            hwr = (home_m['result'] == 'H').mean()
            hdr = (home_m['result'] == 'D').mean()
            hlr = (home_m['result'] == 'A').mean()
            hgs = home_m['home_score'].mean()
            hgc = home_m['away_score'].mean()
        else:
            hwr = hdr = hlr = hgs = hgc = 0.0

        # ── 客场特征 ──────────────────────────────────────────────────
        if not away_m.empty:
            awr = (away_m['result'] == 'A').mean()
            adr = (away_m['result'] == 'D').mean()
            alr = (away_m['result'] == 'H').mean()
            ags = away_m['away_score'].mean()
            agc = away_m['home_score'].mean()
        else:
            awr = adr = alr = ags = agc = 0.0

        # ── 综合近期状态 ──────────────────────────────────────────────
        # 把主客场合并，按时间取最近10场
        combined = pd.concat([
            home_m[['match_datetime', 'home_score', 'away_score', 'result']].assign(
                is_home=True,
                team_score=home_m['home_score'],
                opp_score=home_m['away_score']
            ),
            away_m[['match_datetime', 'home_score', 'away_score', 'result']].assign(
                is_home=False,
                team_score=away_m['away_score'],
                opp_score=away_m['home_score']
            ),
        ]).sort_values('match_datetime').tail(lookback)

        if not combined.empty:
            wins = ((combined['is_home']) & (combined['result'] == 'H')) | \
                   ((~combined['is_home']) & (combined['result'] == 'A'))
            overall_win_rate = wins.mean()
            goal_diff_avg = (combined['team_score'] - combined['opp_score']).mean()

            # 近5场积分（胜3分，平1分）
            last5 = combined.tail(5)
            pts = 0
            for _, r in last5.iterrows():
                if (r['is_home'] and r['result'] == 'H') or (not r['is_home'] and r['result'] == 'A'):
                    pts += 3
                elif r['result'] == 'D':
                    pts += 1
            recent_form = pts / (len(last5) * 3) if len(last5) > 0 else 0.0
        else:
            overall_win_rate = goal_diff_avg = recent_form = 0.0

        stats[team] = {
            'home_win_rate':         round(hwr, 4),
            'home_draw_rate':        round(hdr, 4),
            'home_loss_rate':        round(hlr, 4),
            'home_goals_scored_avg': round(hgs, 4),
            'home_goals_conceded_avg': round(hgc, 4),
            'away_win_rate':         round(awr, 4),
            'away_draw_rate':        round(adr, 4),
            'away_loss_rate':        round(alr, 4),
            'away_goals_scored_avg': round(ags, 4),
            'away_goals_conceded_avg': round(agc, 4),
            'overall_win_rate':      round(overall_win_rate, 4),
            'recent_form':           round(recent_form, 4),
            'goal_diff_avg':         round(goal_diff_avg, 4),
            'home_matches_count':    len(home_m),
            'away_matches_count':    len(away_m),
        }

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# H2H（历史交锋）特征
# ─────────────────────────────────────────────────────────────────────────────

def compute_h2h(df: pd.DataFrame, home_team: str, away_team: str, n: int = 5) -> dict:
    """计算两队历史交锋统计（最近 n 次）"""
    mask = (
        ((df['home_team'] == home_team) & (df['away_team'] == away_team)) |
        ((df['home_team'] == away_team) & (df['away_team'] == home_team))
    )
    h2h = df[mask].sort_values('match_datetime').tail(n)

    if h2h.empty:
        return {'h2h_home_wins': 0, 'h2h_draws': 0, 'h2h_away_wins': 0, 'h2h_total': 0}

    home_wins = ((h2h['home_team'] == home_team) & (h2h['result'] == 'H')).sum() + \
                ((h2h['away_team'] == home_team) & (h2h['result'] == 'A')).sum()
    draws = (h2h['result'] == 'D').sum()
    away_wins = len(h2h) - home_wins - draws

    return {
        'h2h_home_wins': int(home_wins),
        'h2h_draws':     int(draws),
        'h2h_away_wins': int(away_wins),
        'h2h_total':     len(h2h),
        'h2h_home_win_rate': round(home_wins / len(h2h), 4) if len(h2h) > 0 else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 为每场比赛组装特征向量（用于 ML 训练）
# ─────────────────────────────────────────────────────────────────────────────

def build_match_feature_matrix(df: pd.DataFrame, team_stats: dict) -> pd.DataFrame:
    """
    为每场已完赛比赛构建特征矩阵，每行对应一场比赛。
    特征 = 主队统计 + 客队统计 + H2H 统计
    标签 = full_time_result (H/D/A)
    """
    rows = []
    feat_cols = [
        'home_win_rate', 'home_draw_rate', 'home_loss_rate',
        'home_goals_scored_avg', 'home_goals_conceded_avg',
        'away_win_rate', 'away_draw_rate', 'away_loss_rate',
        'away_goals_scored_avg', 'away_goals_conceded_avg',
        'overall_win_rate', 'recent_form', 'goal_diff_avg',
    ]

    for _, match in df.iterrows():
        ht = match['home_team']
        at = match['away_team']

        if ht not in team_stats or at not in team_stats:
            continue

        hs = team_stats[ht]
        as_ = team_stats[at]
        h2h = compute_h2h(df, ht, at)

        row = {
            'match_id':   match['match_id'],
            'match_date': match.get('match_date'),
            'league':     match.get('league_name', ''),
            'home_team':  ht,
            'away_team':  at,
            'result':     match['result'],
        }

        # 主队特征（前缀 h_）
        for col in feat_cols:
            row[f'h_{col}'] = hs.get(col, 0.0)

        # 客队特征（前缀 a_）
        for col in feat_cols:
            row[f'a_{col}'] = as_.get(col, 0.0)

        # 差值特征（主 - 客，量化相对强弱）
        row['diff_win_rate']         = hs['overall_win_rate'] - as_['overall_win_rate']
        row['diff_recent_form']      = hs['recent_form']      - as_['recent_form']
        row['diff_home_scored_avg']  = hs['home_goals_scored_avg']   - as_['away_goals_conceded_avg']
        row['diff_away_concede_avg'] = as_['away_goals_scored_avg']  - hs['home_goals_conceded_avg']

        # H2H
        row.update(h2h)

        rows.append(row)

    feature_df = pd.DataFrame(rows)
    logger.info(f"✅ 构建特征矩阵: {len(feature_df)} 场比赛 × {len(feature_df.columns)} 列")
    return feature_df


# ─────────────────────────────────────────────────────────────────────────────
# 将球队近期状态写入 team_ratings 表
# ─────────────────────────────────────────────────────────────────────────────

def save_team_ratings(team_stats: dict, db=None):
    """将球队统计写入 team_ratings 表，供 AI Prompt 实时查询"""
    if db is None:
        from scripts.database import prediction_db
        db = prediction_db

    saved = 0
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()
            for team, stats in team_stats.items():
                cur.execute("""
                    INSERT INTO team_ratings (team_name, xg_for, xg_against, updated_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (team_name) DO UPDATE SET
                        xg_for        = EXCLUDED.xg_for,
                        xg_against    = EXCLUDED.xg_against,
                        updated_at    = CURRENT_TIMESTAMP
                """, (
                    team,
                    float(stats.get('home_goals_scored_avg', 0.0)),
                    float(stats.get('home_goals_conceded_avg', 0.0)),
                ))
                saved += 1
            conn.commit()
        logger.info(f"✅ 已更新 {saved} 支球队的统计数据到 team_ratings")
    except Exception as e:
        logger.error(f"写入 team_ratings 失败: {e}", exc_info=True)

    return saved


# ─────────────────────────────────────────────────────────────────────────────
# 主入口（可单独运行）
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("🔧 开始特征工程...")
    df = load_historical_matches()
    if df.empty:
        print("❌ 没有历史数据，请先运行 sync_historical.py")
        sys.exit(1)

    print(f"📊 加载了 {len(df)} 场比赛，涉及联赛: {df['league_name'].unique().tolist()}")

    print("⚙️  计算球队近期统计...")
    team_stats = compute_team_stats(df, lookback=10)
    print(f"   共计算 {len(team_stats)} 支球队")

    print("📐 构建特征矩阵...")
    feat_df = build_match_feature_matrix(df, team_stats)
    print(f"   特征矩阵: {feat_df.shape}")

    print("💾 写入球队评分到数据库...")
    save_team_ratings(team_stats)

    # 保存特征矩阵供模型训练使用
    import pickle
    out_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'feature_matrix.pkl')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    feat_df.to_pickle(out_path)
    print(f"✅ 特征矩阵已保存: {os.path.abspath(out_path)}")