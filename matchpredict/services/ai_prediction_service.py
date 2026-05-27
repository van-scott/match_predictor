# -*- coding: utf-8 -*-
"""Business logic for /api/ai/predict."""
from matchpredict.data import prediction_db
from matchpredict.services.ai_prompt_service import (
    build_analysis_prompt,
    calc_implied_probability_str,
)

try:
    from scripts.ai_predictor import AIFootballPredictor
except ImportError:
    AIFootballPredictor = None


class AIPredictionService:
    COST_PER_MATCH = 3

    def predict_matches(self, current_user: dict, matches: list, override_engine=None, override_model=None):
        if not prediction_db:
            raise ValueError('数据库服务不可用')
        if not AIFootballPredictor:
            raise ValueError('AI 预测器不可用')

        total_cost = len(matches) * self.COST_PER_MATCH
        credits = prediction_db.get_user_credits(current_user['id'])
        if credits < total_cost:
            return {
                'success': False,
                'error_code': 'INSUFFICIENT_CREDITS',
                'message': f'积分不足（需要{total_cost}积分，当前{credits}积分）',
                'cost': total_cost,
                'current_credits': credits,
                '_status': 402,
            }

        if not prediction_db.deduct_credits(current_user['id'], total_cost):
            return {'success': False, 'message': '扣积分失败，请稍后重试', '_status': 500}

        predictor = AIFootballPredictor(
            user_id=current_user['id'],
            override_engine=override_engine,
            override_model=override_model,
        )

        if predictor.engine_type == 'system' and not predictor.api_key:
            return {'success': False, 'message': '系统 AI 服务未配置，请在管理后台设置', '_status': 500}
        if predictor.engine_type == 'api_key' and not predictor.api_key:
            return {'success': False, 'message': '您选择使用自定义 API Key，但未在个人中心配置 Key', '_status': 500}
        if predictor.engine_type == 'kiro_cli' and not predictor.cli_path_kiro:
            return {'success': False, 'message': 'Kiro CLI 路径未配置', '_status': 500}
        if predictor.engine_type == 'antigravity_cli' and not predictor.cli_path_antigravity:
            return {'success': False, 'message': 'Antigravity CLI 路径未配置', '_status': 500}
        if predictor.engine_type == 'cursor_cli' and not predictor.cli_path_cursor:
            return {'success': False, 'message': 'Cursor CLI 路径未配置', '_status': 500}

        predictions = []
        for m in matches:
            home = m.get('home_team', '') or m.get('home', '')
            away = m.get('away_team', '') or m.get('away', '')
            home_cn = m.get('home_team_cn', '') or home
            away_cn = m.get('away_team_cn', '') or away
            league = m.get('league_name', '') or m.get('league', '')
            ho = m.get('home_odds') or (m.get('odds') or {}).get('h') or (m.get('odds') or {}).get('hhad', {}).get('h', '-')
            do_ = m.get('draw_odds') or (m.get('odds') or {}).get('d') or (m.get('odds') or {}).get('hhad', {}).get('d', '-')
            ao = m.get('away_odds') or (m.get('odds') or {}).get('a') or (m.get('odds') or {}).get('hhad', {}).get('a', '-')
            t = m.get('match_time', '') or m.get('match_date', '')

            prob_str = calc_implied_probability_str(ho, do_, ao)
            # D2: 传入 ML 预测概率（如有），让 AI 以统计模型结果为背景信号
            ml_probs = m.get('ml_prediction') or m.get('ml_probs')
            prompt = build_analysis_prompt(
                home, away, league, t, str(ho), str(do_), str(ao), prob_str,
                ml_probs=ml_probs,
            )
            try:
                analysis = predictor._call_ai_model(prompt)
                if not analysis:
                    analysis = '⚠️ AI未返回分析内容'
            except Exception as e:
                analysis = f'⚠️ AI分析失败：{str(e)[:100]}'

            predictions.append({
                'match_id': m.get('match_id') or m.get('fixture_id', ''),
                'home_team': home,
                'away_team': away,
                'home_team_cn': home_cn,
                'away_team_cn': away_cn,
                'league_name': league,
                'match_time': t,
                'home_odds': ho,
                'draw_odds': do_,
                'away_odds': ao,
                'ai_analysis': analysis,
            })

        credits_after = prediction_db.get_user_credits(current_user['id'])
        return {
            'success': True,
            'predictions': predictions,
            'credits_deducted': total_cost,
            'credits_remaining': credits_after,
            '_status': 200,
        }

    def save_results(self, current_user: dict | None, results: list) -> dict:
        """Save AI analyses into match_predictions table."""
        if not results or not prediction_db or not current_user:
            return {'success': True, 'saved': 0}
        saved = 0
        for r in results:
            try:
                match_data = {
                    'home_team': r.get('home_team', ''),
                    'away_team': r.get('away_team', ''),
                    'league_name': r.get('league_name', ''),
                    'match_time': r.get('match_time', ''),
                    'home_odds': r.get('home_odds') or (r.get('odds', {}) or {}).get('home'),
                    'draw_odds': r.get('draw_odds') or (r.get('odds', {}) or {}).get('draw'),
                    'away_odds': r.get('away_odds') or (r.get('odds', {}) or {}).get('away'),
                }
                prediction_db.save_ai_prediction(
                    match_data=match_data,
                    prediction_result=r.get('predicted_result', ''),
                    confidence=0.0,
                    ai_analysis=r.get('ai_analysis', ''),
                    user_ip=r.get('user_ip') or '',
                    user_id=current_user['id'],
                    username=current_user['username'],
                )
                saved += 1
            except Exception:
                # 控制器已经打日志，这里静默累积
                continue
        return {'success': True, 'saved': saved, '_status': 200}


ai_prediction_service = AIPredictionService()
