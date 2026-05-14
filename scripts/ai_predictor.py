#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据驱动的足球比赛 AI 分析预测模块
调用 Gemini 前先从数据库加载球队统计数据，将量化特征注入 Prompt。
同时集成本地 ML 模型，融合概率作为最终预测。
"""
import json
import logging
import time
import os
import random
import sys
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimpleMatchAnalysis:
    """比赛分析结果"""
    match_id:      str
    home_team:     str
    away_team:     str
    league_name:   str
    ai_analysis:   str
    home_odds:     float
    draw_odds:     float
    away_odds:     float
    ml_proba:      Dict[str, float] = field(default_factory=dict)  # 本地 ML 概率
    recommendation: str = ""                                         # 综合推荐


# ─────────────────────────────────────────────────────────────────────────────
# 数据库查询：球队近期状态
# ─────────────────────────────────────────────────────────────────────────────

def fetch_team_context(home_team: str, away_team: str, db=None) -> Dict[str, Any]:
    """
    从 historical_matches 表查询球队近期表现（最近10场）。
    返回结构化字典，供 Prompt 使用。
    """
    if db is None:
        try:
            from scripts.database import prediction_db
            db = prediction_db
        except Exception:
            return {}

    ctx = {}
    try:
        with db.get_db_connection() as conn:
            cur = conn.cursor()

            for role, team in [('home', home_team), ('away', away_team)]:
                # 主/客场各自最近10场
                cur.execute("""
                    SELECT full_time_home_goals, full_time_away_goals, full_time_result,
                           half_time_home_goals, half_time_away_goals,
                           CASE WHEN home_team=%s THEN 'home' ELSE 'away' END as side,
                           match_datetime
                    FROM historical_matches
                    WHERE (home_team=%s OR away_team=%s)
                      AND full_time_result IS NOT NULL
                    ORDER BY match_datetime DESC
                    LIMIT 10
                """, (team, team, team))
                rows = cur.fetchall()

                wins = draws = losses = goals_for = goals_against = 0
                form_str = ""
                for r in rows:
                    hg, ag, res, _, _, side, _ = r
                    if side == 'home':
                        gf, ga = (hg or 0), (ag or 0)
                        won = res == 'H'
                        drew = res == 'D'
                    else:
                        gf, ga = (ag or 0), (hg or 0)
                        won = res == 'A'
                        drew = res == 'D'
                    goals_for += gf
                    goals_against += ga
                    if won:
                        wins += 1
                        form_str += 'W'
                    elif drew:
                        draws += 1
                        form_str += 'D'
                    else:
                        losses += 1
                        form_str += 'L'

                n = len(rows)
                ctx[role] = {
                    'team':       team,
                    'matches':    n,
                    'wins':       wins,
                    'draws':      draws,
                    'losses':     losses,
                    'win_rate':   round(wins / n, 3) if n else 0,
                    'goals_for_avg':     round(goals_for / n, 2) if n else 0,
                    'goals_against_avg': round(goals_against / n, 2) if n else 0,
                    'goal_diff_avg':     round((goals_for - goals_against) / n, 2) if n else 0,
                    'form':       form_str[:5],  # 最近5场形态字符串
                }

                # H2H（历史交锋，最近5次）
                if role == 'away':
                    cur.execute("""
                        SELECT full_time_result, home_team
                        FROM historical_matches
                        WHERE ((home_team=%s AND away_team=%s) OR (home_team=%s AND away_team=%s))
                          AND full_time_result IS NOT NULL
                        ORDER BY match_datetime DESC LIMIT 5
                    """, (home_team, away_team, away_team, home_team))
                    h2h_rows = cur.fetchall()
                    h_wins = sum(1 for r in h2h_rows if (r[0]=='H' and r[1]==home_team) or (r[0]=='A' and r[1]==away_team))
                    a_wins = sum(1 for r in h2h_rows if (r[0]=='A' and r[1]==away_team) or (r[0]=='H' and r[1]==home_team))
                    d_cnt  = sum(1 for r in h2h_rows if r[0]=='D')
                    # 修正：从主队视角
                    h_wins = sum(1 for r in h2h_rows if (r[0]=='H' and r[1]==home_team) or (r[0]=='A' and r[1]!=home_team))
                    ctx['h2h'] = {
                        'total':     len(h2h_rows),
                        'home_wins': h_wins,
                        'draws':     d_cnt,
                        'away_wins': len(h2h_rows) - h_wins - d_cnt,
                    }

    except Exception as e:
        logger.warning(f"查询球队上下文失败: {e}")

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 本地 ML 模型推理
# ─────────────────────────────────────────────────────────────────────────────

def ml_predict(home_team: str, away_team: str) -> Dict[str, float]:
    """
    调用本地训练好的 ML 模型，返回胜平负概率。
    如果模型不存在则返回空字典。
    """
    model_path = os.path.join(
        os.path.dirname(__file__), '..', 'models', 'match_predictor_all.pkl'
    )
    if not os.path.exists(model_path):
        return {}

    try:
        import pickle
        with open(model_path, 'rb') as f:
            model_pkg = pickle.load(f)

        from scripts.feature_engineering import (
            load_historical_matches, compute_team_stats
        )
        from scripts.train_model import predict_probabilities

        df = load_historical_matches()
        if df.empty:
            return {}
        team_stats = compute_team_stats(df, lookback=10)
        proba = predict_probabilities(home_team, away_team, team_stats, df, model_pkg)
        return proba
    except Exception as e:
        logger.warning(f"ML 推理失败: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 构建数据驱动的 Prompt
# ─────────────────────────────────────────────────────────────────────────────

def build_rich_prompt(match: Dict[str, Any], ctx: Dict[str, Any],
                      ml_proba: Dict[str, float]) -> str:
    """
    将量化数据注入 Prompt，让 AI 基于真实统计分析而非"常识"。
    """
    home_team  = match.get('home_team', '主队')
    away_team  = match.get('away_team', '客队')
    league     = match.get('league_name', '未知联赛')

    odds       = match.get('odds', {})
    hhad       = odds.get('hhad', {})
    home_odds  = float(hhad.get('h', match.get('home_odds', 2.0)))
    draw_odds  = float(hhad.get('d', match.get('draw_odds', 3.2)))
    away_odds  = float(hhad.get('a', match.get('away_odds', 2.8)))

    # ── 球队上下文块 ─────────────────────────────────────────────────────────
    home_ctx = ctx.get('home', {})
    away_ctx = ctx.get('away', {})
    h2h      = ctx.get('h2h', {})

    def fmt_team(c: dict, label: str) -> str:
        if not c:
            return f"{label}: 暂无历史数据"
        return (
            f"{label}（近{c['matches']}场）：\n"
            f"  · 胜/平/负：{c['wins']}/{c['draws']}/{c['losses']}，"
            f"胜率 {c['win_rate']*100:.1f}%\n"
            f"  · 进球均值 {c['goals_for_avg']}，"
            f"失球均值 {c['goals_against_avg']}，"
            f"净球 {c['goal_diff_avg']:+.2f}\n"
            f"  · 近期状态：{c['form'] or 'N/A'} （W=胜, D=平, L=负）"
        )

    h2h_str = ""
    if h2h and h2h.get('total', 0) > 0:
        h2h_str = (
            f"\n【历史交锋（近{h2h['total']}次）】\n"
            f"  {home_team} 赢 {h2h['home_wins']} 次 | "
            f"平 {h2h['draws']} 次 | "
            f"{away_team} 赢 {h2h['away_wins']} 次"
        )

    # ── ML 模型概率块 ───────────────────────────────────────────────────────
    ml_str = ""
    if ml_proba:
        ph = ml_proba.get('H', 0)
        pd_ = ml_proba.get('D', 0)
        pa = ml_proba.get('A', 0)
        ml_str = (
            f"\n【量化模型概率（基于3500+场历史数据训练的 ML 模型）】\n"
            f"  主胜概率 {ph*100:.1f}%  |  平局 {pd_*100:.1f}%  |  客胜 {pa*100:.1f}%"
        )

    prompt = f"""你是一位专业足球分析师，请基于以下量化数据对这场比赛做出全面分析和预测。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【比赛信息】
{home_team} vs {away_team}
联赛：{league}
赔率：主胜 {home_odds:.2f} | 平局 {draw_odds:.2f} | 客胜 {away_odds:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【量化统计数据（来自真实历史数据库）】

{fmt_team(home_ctx, f'🏠 {home_team}')}

{fmt_team(away_ctx, f'✈️ {away_team}')}
{h2h_str}
{ml_str}

赔率隐含概率：主胜 {1/home_odds/(1/home_odds+1/draw_odds+1/away_odds)*100:.1f}% | 平 {1/draw_odds/(1/home_odds+1/draw_odds+1/away_odds)*100:.1f}% | 客胜 {1/away_odds/(1/home_odds+1/draw_odds+1/away_odds)*100:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【请按以下格式给出分析】

**一、关键因素分析**
（基于上方量化数据，点评主客队近期状态、历史交锋及赔率信号）

**二、胜平负预测**
推荐：[主胜/平局/客胜]
置信度：[1-10]
理由：（需引用具体数据支撑，而非泛泛而谈）

**三、比分预测**
最可能比分：X:X（说明依据）

**四、大小球**
大/小球预测：（依据两队进失球均值）

**五、风险提示**
（识别不确定因素）

请用中文回答，分析要简练有力，每条理由必须有数字支撑。"""

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# AI 预测器主类
# ─────────────────────────────────────────────────────────────────────────────

class AIFootballPredictor:
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash-exp"):
        self.api_key   = api_key
        self.model_name = model_name
        self.base_url  = "https://generativelanguage.googleapis.com/v1beta/models"

    # ── 批量分析 ──────────────────────────────────────────────────────────────
    def analyze_matches(self, matches: List[Dict[str, Any]],
                        use_db: bool = True) -> List[SimpleMatchAnalysis]:
        analyses = []
        for match in matches:
            try:
                analysis = self._analyze_single_match(match, use_db=use_db)
                if analysis:
                    analyses.append(analysis)
            except Exception as e:
                ht = match.get('home_team', '')
                at = match.get('away_team', '')
                logger.error(f"分析 {ht} vs {at} 失败: {e}")
                analyses.append(self._create_error_analysis(match, str(e)))
        return analyses

    # ── 单场分析 ──────────────────────────────────────────────────────────────
    def _analyze_single_match(self, match: Dict[str, Any],
                               use_db: bool = True) -> Optional[SimpleMatchAnalysis]:
        home_team  = match.get('home_team', '')
        away_team  = match.get('away_team', '')
        league     = match.get('league_name', '未知联赛')
        odds       = match.get('odds', {})
        hhad       = odds.get('hhad', {})
        home_odds  = float(hhad.get('h', match.get('home_odds', 2.0)))
        draw_odds  = float(hhad.get('d', match.get('draw_odds', 3.2)))
        away_odds  = float(hhad.get('a', match.get('away_odds', 2.8)))

        # 1. 拉球队上下文
        ctx = fetch_team_context(home_team, away_team) if use_db else {}

        # 2. 本地 ML 概率
        ml_proba = ml_predict(home_team, away_team)

        # 3. 构建富 Prompt
        prompt = build_rich_prompt(match, ctx, ml_proba)

        # 4. 调用 Gemini
        ai_response = self._call_ai_model(prompt)

        # 5. 综合推荐
        recommendation = self._recommend(ml_proba, home_odds, draw_odds, away_odds)

        if ai_response:
            return SimpleMatchAnalysis(
                match_id        = match.get('match_id', f"match_{int(time.time())}"),
                home_team       = home_team,
                away_team       = away_team,
                league_name     = league,
                ai_analysis     = ai_response,
                home_odds       = home_odds,
                draw_odds       = draw_odds,
                away_odds       = away_odds,
                ml_proba        = ml_proba,
                recommendation  = recommendation,
            )
        return None

    @staticmethod
    def _recommend(ml_proba: dict, home_odds: float,
                   draw_odds: float, away_odds: float) -> str:
        """
        Kelly 边际：只有当 ML 概率比赔率隐含概率高出 5% 以上才推荐。
        """
        if not ml_proba:
            total = 1/home_odds + 1/draw_odds + 1/away_odds
            implied = {'H': 1/home_odds/total, 'D': 1/draw_odds/total, 'A': 1/away_odds/total}
        else:
            implied = {}
            total = 1/home_odds + 1/draw_odds + 1/away_odds
            implied = {
                'H': 1/home_odds/total,
                'D': 1/draw_odds/total,
                'A': 1/away_odds/total,
            }

        label_map = {'H': '主胜', 'D': '平局', 'A': '客胜'}
        if ml_proba:
            best = max(ml_proba, key=ml_proba.get)
            edge = ml_proba[best] - implied.get(best, 0)
            if edge >= 0.05:
                return f"推荐{label_map[best]}（ML优势 {edge*100:.1f}%）"
            else:
                return f"谨慎（ML与市场差值<5%，{label_map[best]}概率{ml_proba[best]*100:.1f}%）"
        else:
            best = max(implied, key=implied.get)
            return f"市场偏{label_map[best]}（无ML数据）"

    # ── 调用 Gemini API ───────────────────────────────────────────────────────
    def _call_ai_model(self, prompt: str) -> Optional[str]:
        url = f"{self.base_url}/{self.model_name}:generateContent"
        headers = {
            "Content-Type":  "application/json",
            "x-goog-api-key": self.api_key,
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature":     0.4,   # 降低随机性，让输出更严谨
                "topK":            40,
                "topP":            0.90,
                "maxOutputTokens": 1200,
            },
        }

        for attempt in range(3):
            try:
                logger.info(f"调用 Gemini API (attempt {attempt+1}/3)")
                resp = requests.post(url, headers=headers, json=payload, timeout=30)

                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get('candidates', [])
                    if candidates:
                        return candidates[0]['content']['parts'][0]['text'].strip()
                    return None

                elif resp.status_code == 429:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    logger.warning(f"速率限制，等待 {delay:.1f}s")
                    time.sleep(delay)

                else:
                    logger.error(f"API 错误 {resp.status_code}: {resp.text[:200]}")
                    if attempt == 2:
                        return None

            except requests.exceptions.Timeout:
                logger.warning(f"超时 (attempt {attempt+1})")
                time.sleep(attempt + 1)
            except Exception as e:
                logger.error(f"调用异常: {e}")
                if attempt == 2:
                    return None

        return None

    # ── 错误分析占位 ─────────────────────────────────────────────────────────
    def _create_error_analysis(self, match: Dict, error_msg: str) -> SimpleMatchAnalysis:
        return SimpleMatchAnalysis(
            match_id       = match.get('match_id', f"err_{int(time.time())}"),
            home_team      = match.get('home_team', '未知'),
            away_team      = match.get('away_team', '未知'),
            league_name    = match.get('league_name', '未知联赛'),
            ai_analysis    = f"AI 分析暂时不可用。\n错误信息：{error_msg}",
            home_odds      = 2.0,
            draw_odds      = 3.2,
            away_odds      = 2.8,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 命令行快速测试
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("❌ 请设置 GEMINI_API_KEY 环境变量")
        sys.exit(1)

    predictor = AIFootballPredictor(api_key)

    sample = {
        'match_id':   'test_001',
        'home_team':  'Arsenal FC',
        'away_team':  'Chelsea FC',
        'league_name': '英超',
        'odds': {'hhad': {'h': '1.90', 'd': '3.50', 'a': '3.80'}},
    }

    results = predictor.analyze_matches([sample])
    for r in results:
        print(f"\n{'='*60}")
        print(f"⚽ {r.home_team} vs {r.away_team}")
        print(f"🤖 ML 概率: {r.ml_proba}")
        print(f"💡 综合推荐: {r.recommendation}")
        print(f"\n{r.ai_analysis}")