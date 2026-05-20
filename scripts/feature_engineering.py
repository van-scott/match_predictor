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
import warnings
import numpy as np
import pandas as pd
from datetime import datetime

# 抑制 pandas 对 psycopg2 连接的 UserWarning（功能正常，只是不推荐）
warnings.filterwarnings('ignore', message='.*pandas only supports SQLAlchemy.*')

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
    计算每支球队的近期统计特征（增强版）。
    
    返回: {team_name: {feature_name: value, ...}, ...}
    特征维度:
      home_win_rate, home_draw_rate, home_loss_rate
      away_win_rate, away_draw_rate, away_loss_rate
      home_goals_scored_avg, home_goals_conceded_avg
      away_goals_scored_avg, away_goals_conceded_avg
      overall_win_rate, recent_form (最近5场积分比率)
      goal_diff_avg (近10场净球)
      --- 新增 ---
      recent_form_3:  近3场积分率
      recent_form_5:  近5场积分率
      scoring_trend:  近5场场均进球
      conceding_trend: 近5场场均失球
      home_attack_strength:  主队主场进攻强度
      home_defense_strength: 主队主场防守强度
      away_attack_strength:  客队客场进攻强度
      away_defense_strength: 客队客场防守强度
      win_streak:  当前连胜场次
      loss_streak: 当前连败场次
      unbeaten_run: 不败场次
    """
    all_teams = sorted(set(df['home_team'].unique()) | set(df['away_team'].unique()))
    stats = {}

    # 计算联赛平均值（用于泊松强度计算）
    league_home_goals_avg = df['home_score'].mean() if not df.empty else 1.3
    league_away_goals_avg = df['away_score'].mean() if not df.empty else 1.1

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

            # ── 新增：近3场积分率 ─────────────────────────────────────
            last3 = combined.tail(3)
            pts3 = 0
            for _, r in last3.iterrows():
                if (r['is_home'] and r['result'] == 'H') or (not r['is_home'] and r['result'] == 'A'):
                    pts3 += 3
                elif r['result'] == 'D':
                    pts3 += 1
            recent_form_3 = pts3 / (len(last3) * 3) if len(last3) > 0 else 0.0

            # ── 新增：近5场积分率（独立计算，与 recent_form 相同但命名更清晰）
            recent_form_5 = recent_form

            # ── 新增：近5场进球/失球趋势 ──────────────────────────────
            scoring_trend = last5['team_score'].mean() if len(last5) > 0 else 0.0
            conceding_trend = last5['opp_score'].mean() if len(last5) > 0 else 0.0

            # ── 新增：连胜/连败/不败 ─────────────────────────────────
            win_streak = 0
            loss_streak = 0
            unbeaten_run = 0
            # 从最近一场往前数
            for _, r in combined.iloc[::-1].iterrows():
                is_win = (r['is_home'] and r['result'] == 'H') or (not r['is_home'] and r['result'] == 'A')
                is_loss = (r['is_home'] and r['result'] == 'A') or (not r['is_home'] and r['result'] == 'H')
                # 连胜
                if win_streak == (len(combined) - 1 - combined.iloc[::-1].index.get_loc(_)):
                    pass  # 已经中断
                # 简化：直接计算
                break

            # 重新计算连胜/连败/不败（从最近往前）
            win_streak = 0
            loss_streak = 0
            unbeaten_run = 0
            for idx in range(len(combined) - 1, -1, -1):
                r = combined.iloc[idx]
                is_win = (r['is_home'] and r['result'] == 'H') or (not r['is_home'] and r['result'] == 'A')
                is_loss = (r['is_home'] and r['result'] == 'A') or (not r['is_home'] and r['result'] == 'H')
                is_draw = r['result'] == 'D'

                if idx == len(combined) - 1:
                    # 最近一场
                    if is_win:
                        win_streak = 1
                        unbeaten_run = 1
                    elif is_loss:
                        loss_streak = 1
                    else:
                        unbeaten_run = 1
                else:
                    # 连胜
                    if is_win and win_streak > 0:
                        win_streak += 1
                        unbeaten_run += 1
                    elif not is_loss and unbeaten_run > 0 and win_streak == 0:
                        unbeaten_run += 1
                    elif is_loss and loss_streak > 0:
                        loss_streak += 1
                    else:
                        break
        else:
            overall_win_rate = goal_diff_avg = recent_form = 0.0
            recent_form_3 = recent_form_5 = 0.0
            scoring_trend = conceding_trend = 0.0
            win_streak = loss_streak = unbeaten_run = 0

        # ── 泊松攻防强度 ─────────────────────────────────────────────
        home_attack_strength = (hgs / league_home_goals_avg) if league_home_goals_avg > 0 else 1.0
        home_defense_strength = (hgc / league_away_goals_avg) if league_away_goals_avg > 0 else 1.0
        away_attack_strength = (ags / league_away_goals_avg) if league_away_goals_avg > 0 else 1.0
        away_defense_strength = (agc / league_home_goals_avg) if league_home_goals_avg > 0 else 1.0

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
            # ── 新增特征 ──────────────────────────────────────────────
            'recent_form_3':         round(recent_form_3, 4),
            'recent_form_5':         round(recent_form_5, 4),
            'scoring_trend':         round(scoring_trend, 4),
            'conceding_trend':       round(conceding_trend, 4),
            'home_attack_strength':  round(home_attack_strength, 4),
            'home_defense_strength': round(home_defense_strength, 4),
            'away_attack_strength':  round(away_attack_strength, 4),
            'away_defense_strength': round(away_defense_strength, 4),
            'win_streak':            win_streak,
            'loss_streak':           loss_streak,
            'unbeaten_run':          unbeaten_run,
        }

    logger.info(f"✅ 计算了 {len(stats)} 支球队的增强统计特征")
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

def build_match_feature_matrix(df: pd.DataFrame, team_stats: dict, odds_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    为每场已完赛比赛构建特征矩阵，每行对应一场比赛。
    特征 = 主队统计 + 客队统计 + H2H 统计 + 泊松期望 + 赔率隐含概率（如有）
    标签 = full_time_result (H/D/A)
    
    Args:
        df: 历史比赛 DataFrame
        team_stats: compute_team_stats 返回的球队统计字典
        odds_df: 可选，赔率 DataFrame（含 home_team, away_team, match_date, home_odds, draw_odds, away_odds）
    """
    rows = []
    feat_cols = [
        'home_win_rate', 'home_draw_rate', 'home_loss_rate',
        'home_goals_scored_avg', 'home_goals_conceded_avg',
        'away_win_rate', 'away_draw_rate', 'away_loss_rate',
        'away_goals_scored_avg', 'away_goals_conceded_avg',
        'overall_win_rate', 'recent_form', 'goal_diff_avg',
        # 新增特征
        'recent_form_3', 'recent_form_5',
        'scoring_trend', 'conceding_trend',
        'home_attack_strength', 'home_defense_strength',
        'away_attack_strength', 'away_defense_strength',
        'win_streak', 'loss_streak', 'unbeaten_run',
    ]

    # 联赛平均进球（用于泊松期望计算）
    league_home_avg = df['home_score'].mean() if not df.empty else 1.3
    league_away_avg = df['away_score'].mean() if not df.empty else 1.1

    # 构建赔率查找表（如果有赔率数据）
    odds_lookup = {}
    if odds_df is not None and not odds_df.empty:
        for _, o in odds_df.iterrows():
            key = (str(o.get('home_team', '')), str(o.get('away_team', '')), str(o.get('match_date', '')))
            odds_lookup[key] = (o.get('home_odds'), o.get('draw_odds'), o.get('away_odds'))

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

        # 新增差值特征
        row['diff_scoring_trend']    = hs.get('scoring_trend', 0) - as_.get('scoring_trend', 0)
        row['diff_conceding_trend']  = hs.get('conceding_trend', 0) - as_.get('conceding_trend', 0)
        row['diff_form_3']           = hs.get('recent_form_3', 0) - as_.get('recent_form_3', 0)
        row['diff_win_streak']       = hs.get('win_streak', 0) - as_.get('win_streak', 0)

        # 泊松期望进球
        home_attack = hs.get('home_attack_strength', 1.0)
        away_defense = as_.get('away_defense_strength', 1.0)
        away_attack = as_.get('away_attack_strength', 1.0)
        home_defense = hs.get('home_defense_strength', 1.0)
        row['poisson_home_goals'] = round(home_attack * away_defense * league_home_avg, 4)
        row['poisson_away_goals'] = round(away_attack * home_defense * league_away_avg, 4)
        row['poisson_goal_diff']  = row['poisson_home_goals'] - row['poisson_away_goals']

        # H2H
        row.update(h2h)

        # 赔率隐含概率特征（如果有赔率数据）
        match_date_str = str(match.get('match_date', ''))
        odds_key = (ht, at, match_date_str)
        if odds_key in odds_lookup:
            ho, do, ao = odds_lookup[odds_key]
            if ho and do and ao:
                ho, do, ao = float(ho), float(do), float(ao)
                total = 1/ho + 1/do + 1/ao
                row['odds_home_prob'] = round((1/ho) / total, 4)
                row['odds_draw_prob'] = round((1/do) / total, 4)
                row['odds_away_prob'] = round((1/ao) / total, 4)
                row['odds_overround']  = round(total - 1, 4)  # 博彩公司利润率
                # 过热信号：最热门概率 > 0.65
                max_prob = max(row['odds_home_prob'], row['odds_draw_prob'], row['odds_away_prob'])
                row['odds_favorite_overbet'] = 1 if max_prob > 0.65 else 0
                row['has_odds'] = 1
            else:
                row['odds_home_prob'] = 0.0
                row['odds_draw_prob'] = 0.0
                row['odds_away_prob'] = 0.0
                row['odds_overround'] = 0.0
                row['odds_favorite_overbet'] = 0
                row['has_odds'] = 0
        else:
            row['odds_home_prob'] = 0.0
            row['odds_draw_prob'] = 0.0
            row['odds_away_prob'] = 0.0
            row['odds_overround'] = 0.0
            row['odds_favorite_overbet'] = 0
            row['has_odds'] = 0

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