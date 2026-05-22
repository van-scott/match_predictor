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
    match_time = match.get('match_time') or match.get('match_date', '未知时间')

    odds       = match.get('odds', {})
    hhad       = odds.get('hhad', {})
    odds_type  = odds.get('type', 'had')
    goal_line  = match.get('goal_line') or odds.get('goal_line') or ''
    
    # 区分不让球与让球赔率
    input_odds_str = ""
    home_odds = 2.0
    draw_odds = 3.2
    away_odds = 2.8
    
    if odds_type == 'had':
        home_odds = float(hhad.get('h', match.get('home_odds', 2.0)))
        draw_odds = float(hhad.get('d', match.get('draw_odds', 3.2)))
        away_odds = float(hhad.get('a', match.get('away_odds', 2.8)))
        input_odds_str = f"【提供的不让球赔率】：主胜 {home_odds:.2f} | 平局 {draw_odds:.2f} | 客胜 {away_odds:.2f}"
    else:
        # 如果只有让球赔率
        handicap_h = float(hhad.get('h', 2.0))
        handicap_d = float(hhad.get('d', 3.2))
        handicap_a = float(hhad.get('a', 2.8))
        if not goal_line:
            goal_line = '-1.0'
        input_odds_str = f"【提供的让球赔率】：让球主胜 {handicap_h:.2f} | 让球平局 {handicap_d:.2f} | 让球客胜 {handicap_a:.2f} （让球盘口：{goal_line}）"
        # 估算对应的不让球赔率作为参考
        home_odds = float(match.get('home_odds') or 2.0)
        draw_odds = float(match.get('draw_odds') or 3.2)
        away_odds = float(match.get('away_odds') or 2.8)

    # 计算不让球隐含概率
    sum_inv = (1 / home_odds) + (1 / draw_odds) + (1 / away_odds)
    home_prob = (1 / home_odds) / sum_inv * 100
    draw_prob = (1 / draw_odds) / sum_inv * 100
    away_prob = (1 / away_odds) / sum_inv * 100

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

    prompt = f"""你是一位专业足球分析师。请基于以下量化统计数据和赔率信息，对这场比赛做全面深入的专业分析。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【比赛基础信息】
对阵：{home_team} vs {away_team}
联赛：{league}
比赛时间：{match_time}
{input_odds_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【量化统计数据（来自真实历史数据库）】

{fmt_team(home_ctx, f'🏠 {home_team}')}

{fmt_team(away_ctx, f'✈️ {away_team}')}
{h2h_str}
{ml_str}

不让球赔率隐含概率：主胜 {home_prob:.1f}% | 平局 {draw_prob:.1f}% | 客胜 {away_prob:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 核心指令：格式与术语标准】
你必须完全按照以下格式结构返回分析报告，不能遗漏任何一节，不能更改任何小标题，注意保留所有的连字符 `---` 以及 emoji 符号，使得输出极其专业精美。

### 重要说明（关于让球与不让球赔率）：
- 如果输入中缺少某些赔率，或者没有指明“让球盘口”与“让球赔率”，请作为专家根据“不让球赔率”与两队实力差距，**合理估算、推导并补齐**。
- 让球盘口应以让球个数表示（例如 `主队让1球` 可写为 `-1.0 球`，`主队受让1球` 可写为 `+1.0 球`）。
- 让球赔率应当符合精算概率（例如：若不让球赔率为：主胜 2.46 / 平 2.8 / 客胜 2.74，让球盘口为 -1.0 球时，合理的让球赔率约为：让球主胜 6.1 | 让球平局 3.82 | 让球客胜 1.42。若不让球主胜为 1.5，让球主胜（-1球）约为 2.4）。

请生成以下格式的内容（请直接返回以下Markdown，不要有任何包裹在外的多余废话）：

### {home_team} vs {away_team} 比赛分析报告

比赛信息：
*   联赛： {league}
*   比赛时间： {match_time}
*   赔率： 主胜 {home_odds:.2f} | 平局 {draw_odds:.2f} | 客胜 {away_odds:.2f}
*   让球盘口： [在此处填写合理的让球数，如 -1.0 球]
*   让球赔率： 让球主胜 [在此处推导合理的让球主胜赔率] | 让球平局 [推导合理的让球平局赔率] | 让球客胜 [推导合理的让球客胜赔率]
*   赔率隐含概率： 主胜 {home_prob:.1f}% | 平局 {draw_prob:.1f}% | 客胜 {away_prob:.1f}%

---

📊 一、综合形势分析

1.  赔率隐含概率分析：
    *   [在此处详细解读赔率隐含概率，分析市场倾向]
2.  近期状态：
    *   {home_team}：[分析主队近期战绩、胜率、攻防得失球等表现]
    *   {away_team}：[分析客队近期战绩、胜率、攻防得失球等表现]
3.  历史交锋：
    *   [解读双方历史对战结果及心理优势]
4.  主客场表现：
    *   {home_team}（主场）：[详细点评主队主场优势和战术特征]
    *   {away_team}（客场）：[详细点评客队客场表现与防守抗压能力]

综合判断：
[给出精炼的整体对局总结与盘面分析]

---

🎯 二、胜平负预测

*   推荐结果： [推荐赛果，必须是：主胜、平局 或 客胜]
*   置信度： [高 / 中 / 低]
*   核心理由：
    1.  [第一条核心理由，引用具体数据如进球率、历史战绩支持]
    2.  [第二条核心理由，引用具体数据如主客场胜率或模型概率支持]
    3.  [第三条核心理由，引用具体数据如赔率变动信号支持]

---

⚽ 三、比分预测

*   最可能比分： [例如 1-1] (约[例如 30]%)
    *   理由：[阐述理由]
*   备选比分1： [备选比分，如 0-0]
    *   理由：[阐述理由]
*   备选比分2： [备选比分，如 1-0 (主队胜) 或其他合适比分]
    *   理由：[阐述理由]
*   总进球： [例如 2-3球]
    *   理由：[总进球区间判断理由]

---

⏱️ 四、半全场预测

*   半场： [例如 平局]
    *   理由：[半场倾向研判依据]
*   全场： [例如 平局]
    *   理由：[全场倾向研判依据]

---

💰 五、投注价值

*   最具价值投注项：
    1.  不让球盘口：[如 平局 @2.80]
        *   理由：[选择该项的投注价值与性价比分析]
    2.  让球盘口：[如 让球客胜 (-1.0 球) @1.42]
        *   理由：[选择该项的受让价值与保本防线分析]

---

⚠️ 六、风险提示

*   {home_team}的主场爆发力：[识别并分析主队潜在的主场抢分和定位球突袭风险]
*   {away_team}的客场进攻效率：[识别并分析客队防守反击与客场终结效率的潜在变数]
*   突发伤病或红牌：[总结针对足球比赛无法预测的非控风险，如红黄牌或临场伤退]"""

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# AI 预测器主类
# ─────────────────────────────────────────────────────────────────────────────

class AIFootballPredictor:
    def __init__(self, api_key: str = None, model_name: str = None, user_id: int = None):
        # 优先从数据库读取配置，fallback 到参数/环境变量
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = os.environ.get('GEMINI_BASE_URL') or os.environ.get('GEMINI_API_URL') or "https://generativelanguage.googleapis.com/v1beta/models"
        self.use_openai_format = False
        self.permanent_error = None
        
        # 新增多引擎支持的默认属性
        self.engine_type = 'system'
        self.cli_path_kiro = 'kiro'
        self.cli_path_antigravity = 'antigravity'
        self.cli_path_cursor = 'cursor'
        
        # 1. 尝试从数据库读取用户的个性化 AI 配置
        if user_id:
            try:
                from scripts.database import prediction_db
                if prediction_db:
                    user_cfg = prediction_db.get_user_ai_config(user_id)
                    if user_cfg:
                        self.engine_type = user_cfg.get('ai_engine_type', 'system')
                        if self.engine_type != 'system':
                            # 始终为所有非 system 引擎加载通用自定义/CLI 配置参数（API Key、Base URL 与 Model）
                            self.api_key = user_cfg.get('ai_api_key') or self.api_key
                            self.base_url = user_cfg.get('ai_api_url') or self.base_url
                            if self.base_url:
                                self.base_url = self.base_url.rstrip('/')
                            self.model_name = user_cfg.get('ai_model') or self.model_name

                            if self.engine_type == 'api_key':
                                self.use_openai_format = True
                            
                            # 获取各类 CLI 路径配置
                            self.cli_path_kiro = user_cfg.get('cli_path_kiro', 'kiro')
                            self.cli_path_antigravity = user_cfg.get('cli_path_antigravity', 'antigravity')
                            self.cli_path_cursor = user_cfg.get('cli_path_cursor', 'cursor')
                            return # 成功加载用户定制的非 system 配置，直接返回
            except Exception as e:
                logger.error(f"加载用户个性化 AI 配置失败 (user_id={user_id}): {e}")
        
        # 2. 如果配置为 system 或未提供 user_id，则加载系统全局配置
        try:
            from scripts.database import prediction_db
            if prediction_db:
                with prediction_db.get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT key, value FROM system_config WHERE key IN ('ai_api_url','ai_api_key','ai_model')")
                    cfg = dict(cur.fetchall())
                    if cfg.get('ai_api_key'):
                        self.api_key = cfg['ai_api_key']
                        self.base_url = cfg.get('ai_api_url', self.base_url).rstrip('/')
                        self.model_name = cfg.get('ai_model', self.model_name)
                        self.use_openai_format = True  # 数据库配置的都用 OpenAI 兼容格式
        except Exception:
            pass

    def _is_permanent_error(self, status_code: int, text: str) -> Optional[str]:
        """检测响应是否指示永久性 API 错误（如额度不足、无效 Key）"""
        text_lower = text.lower()
        if status_code == 401:
            return "API Key 无效或未授权，请检查后台配置。"
        
        quota_keywords = [
            "quota", "balance", "exceeded", "insufficient", "limit", 
            "额度", "余额", "欠费", "超限", "次数已达上限"
        ]
        invalid_keywords = [
            "invalid", "key not valid", "api key", "not valid", "无效"
        ]
        
        if status_code in (400, 403, 429):
            for kw in quota_keywords:
                if kw in text_lower:
                    return "API 额度不足或账号余额不足，请联系管理员充值。"
            for kw in invalid_keywords:
                if kw in text_lower:
                    return "API Key 无效或已过期，请检查后台配置。"
                    
        return None

    # ── 批量分析 ──────────────────────────────────────────────────────────────
    def analyze_matches(self, matches: List[Dict[str, Any]],
                        use_db: bool = True) -> List[SimpleMatchAnalysis]:
        analyses = []
        for match in matches:
            try:
                analysis = self._analyze_single_match(match, use_db=use_db)
                if analysis:
                    analyses.append(analysis)
                else:
                    analyses.append(self._create_error_analysis(match, "AI 分析未返回结果"))
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

    # ── 调用 AI 引擎（支持 API 与 CLI） ───────────────────────────────────────
    def _call_ai_model(self, prompt: str) -> Optional[str]:
        if getattr(self, 'permanent_error', None):
            raise RuntimeError(self.permanent_error)
            
        # 根据配置的引擎类型路由请求
        if self.engine_type == 'kiro_cli':
            return self._call_cli_command(self.cli_path_kiro, prompt)
        elif self.engine_type == 'antigravity_cli':
            return self._call_cli_command(self.cli_path_antigravity, prompt)
        elif self.engine_type == 'cursor_cli':
            return self._call_cli_command(self.cli_path_cursor, prompt)

        if self.use_openai_format:
            return self._call_openai_format(prompt)
        return self._call_gemini_native(prompt)

    def _call_cli_command(self, cmd_str: str, prompt: str) -> str:
        """安全并稳健地调用本地命令行 AI 工具（kiro-cli / antigravity cli / cursor cli）"""
        import shlex
        import subprocess

        if not cmd_str:
            raise RuntimeError("命令行路径/指令未配置，请在个人中心填写。")

        try:
            cmd_args = shlex.split(cmd_str)
        except Exception as e:
            raise RuntimeError(f"命令行指令格式非法: {e}")

        logger.info(f"正在调用本地 CLI 预测引擎: {' '.join(cmd_args)}")
        try:
            # 准备环境变量，向 CLI 传递用户在界面配置的密钥、地址和模型
            import os
            env = os.environ.copy()
            if getattr(self, 'api_key', None):
                env['GEMINI_API_KEY'] = self.api_key
                env['OPENAI_API_KEY'] = self.api_key
                env['CURSOR_TOKEN'] = self.api_key
            if getattr(self, 'base_url', None):
                env['GEMINI_API_URL'] = self.base_url
                env['GEMINI_BASE_URL'] = self.base_url
                env['OPENAI_API_BASE'] = self.base_url
            if getattr(self, 'model_name', None):
                env['GEMINI_MODEL'] = self.model_name
                env['OPENAI_MODEL'] = self.model_name
                env['AI_MODEL'] = self.model_name

            # 运行命令并将 prompt 通过 stdin 输入给 CLI
            res = subprocess.run(
                cmd_args,
                input=prompt,
                capture_output=True,
                text=True,
                encoding='utf-8',
                env=env,
                timeout=30
            )
            
            if res.returncode == 0:
                stdout_content = res.stdout.strip()
                if not stdout_content:
                    raise RuntimeError("命令行执行成功，但未返回任何输出。")
                return stdout_content
            else:
                err_msg = (res.stderr or res.stdout or '').strip()
                raise RuntimeError(f"命令行执行失败 (退出码 {res.returncode}): {err_msg[:300]}")
        except FileNotFoundError:
            raise RuntimeError(f"未找到命令行可执行文件 '{cmd_args[0]}'，请检查您的 CLI 是否安装或路径是否配置正确。")
        except subprocess.TimeoutExpired:
            raise RuntimeError("命令行分析超时（限制为 30 秒）。")
        except Exception as e:
            raise RuntimeError(f"调用本地命令行异常: {str(e)}")

    def _call_openai_format(self, prompt: str) -> Optional[str]:
        """使用 OpenAI 兼容格式调用（支持 360 中转、OpenRouter 等）"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "你是一位经验丰富的足球分析专家。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 1500,
        }

        for attempt in range(3):
            try:
                logger.info(f"调用 OpenAI 兼容 API (attempt {attempt+1}/3)")
                resp = requests.post(url, headers=headers, json=payload, timeout=25)
                
                perm_err = self._is_permanent_error(resp.status_code, resp.text)
                if perm_err:
                    self.permanent_error = perm_err
                    raise RuntimeError(perm_err)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    return content.strip() if content else None
                elif resp.status_code == 429:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    logger.warning(f"速率限制，等待 {delay:.1f}s")
                    time.sleep(delay)
                else:
                    logger.error(f"AI API 错误 {resp.status_code}: {resp.text[:200]}")
                    if attempt == 2:
                        return None
            except requests.exceptions.Timeout:
                logger.warning(f"超时 (attempt {attempt+1})")
                if attempt < 2:
                    time.sleep(attempt + 1)
            except RuntimeError as re:
                if getattr(self, 'permanent_error', None) == str(re):
                    raise
                logger.error(f"AI 调用运行时异常: {re}")
                if attempt == 2:
                    return None
            except Exception as e:
                logger.error(f"AI 调用异常: {e}")
                if attempt == 2:
                    return None
        return None

    def _call_gemini_native(self, prompt: str) -> Optional[str]:
        """使用 Google Gemini 原生格式调用"""
        url = f"{self.base_url}/{self.model_name}:generateContent"
        headers = {
            "Content-Type":  "application/json",
            "x-goog-api-key": self.api_key,
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature":     0.4,
                "topK":            40,
                "topP":            0.90,
                "maxOutputTokens": 1200,
            },
        }

        for attempt in range(3):
            try:
                logger.info(f"调用 Gemini API (attempt {attempt+1}/3)")
                resp = requests.post(url, headers=headers, json=payload, timeout=25)

                perm_err = self._is_permanent_error(resp.status_code, resp.text)
                if perm_err:
                    self.permanent_error = perm_err
                    raise RuntimeError(perm_err)

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
                if attempt < 2:
                    time.sleep(attempt + 1)
            except RuntimeError as re:
                if getattr(self, 'permanent_error', None) == str(re):
                    raise
                logger.error(f"Gemini 调用运行时异常: {re}")
                if attempt == 2:
                    return None
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