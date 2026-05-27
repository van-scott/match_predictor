# -*- coding: utf-8 -*-
"""Prediction save, stats, and classic analysis."""
import json
import math
from datetime import datetime

from matchpredict.repositories.prediction_repository import PredictionRepository

CLASSIC_COST = 1


class PredictionService:
    def __init__(self, repo: PredictionRepository | None = None):
        self._repo = repo or PredictionRepository()

    def save_prediction(self, current_user: dict, data: dict, user_ip: str) -> dict:
        if not self._repo._db:
            return {'success': False, 'message': '数据库未配置', '_http_status': 500}
        if not current_user:
            return {'success': False, 'message': '请先登录再进行预测', '_http_status': 401}
        if not self._repo.can_user_predict(
            current_user['id'],
            current_user['user_type'],
            current_user['daily_predictions_used'],
        ):
            return {
                'success': False,
                'message': '今日免费预测次数已用完，请升级会员',
                '_http_status': 403,
            }
        if not data:
            return {'success': False, 'message': '请求数据为空', '_http_status': 400}

        mode = data.get('mode', '').lower()
        match_data = data.get('match_data', {})
        prediction_result = data.get('prediction_result', '')
        confidence = data.get('confidence', 0)
        ai_analysis = data.get('ai_analysis', '')
        uid, uname = current_user['id'], current_user['username']

        if mode == 'ai':
            ok = self._repo.save_ai(
                match_data, prediction_result, confidence, ai_analysis, user_ip, uid, uname
            )
        elif mode == 'classic':
            ok = self._repo.save_classic(
                match_data, prediction_result, confidence, user_ip, uid, uname
            )
        elif mode == 'lottery':
            ok = self._repo.save_lottery(
                match_data, prediction_result, confidence, ai_analysis, user_ip, uid, uname
            )
        else:
            return {'success': False, 'message': '未知的预测模式', '_http_status': 400}

        if not ok:
            return {'success': False, 'message': '预测结果保存失败', '_http_status': 500}

        self._repo.increment_predictions(uid)
        updated = self._repo.get_user_by_username(uname)
        if not updated:
            return {'success': False, 'message': '预测成功，但获取用户状态失败', '_http_status': 500}

        return {
            'success': True,
            'message': '预测结果保存成功',
            '_http_status': 200,
            '_session_user': updated,
            'user': {
                'username': updated['username'],
                'user_type': updated['user_type'],
                'daily_predictions_used': updated['daily_predictions_used'],
                'total_predictions': updated['total_predictions'],
                'membership_expires': updated['membership_expires'].isoformat()
                if updated.get('membership_expires')
                else None,
            },
        }

    def get_stats(self) -> dict:
        if not self._repo._db:
            return {'success': False, 'message': '数据库未配置', '_http_status': 500}
        return {'success': True, 'data': self._repo.get_stats(), '_http_status': 200}

    def analyze_classic(self, current_user: dict, matches: list) -> dict:
        if not current_user:
            return {
                'success': False,
                'message': '请先登录',
                'error_code': 'NOT_LOGGED_IN',
                '_http_status': 401,
            }
        if not self._repo._db:
            return {'success': False, 'message': '数据库服务不可用', '_http_status': 500}
        if not matches:
            return {'success': False, 'message': '未提供比赛数据', '_http_status': 400}

        credits = self._repo.get_user_credits(current_user['id'])
        if credits < CLASSIC_COST:
            return {
                'success': False,
                'error_code': 'INSUFFICIENT_CREDITS',
                'message': f'积分不足（需要{CLASSIC_COST}积分，当前{credits}积分）',
                'cost': CLASSIC_COST,
                'current_credits': credits,
                '_http_status': 402,
            }

        self._repo.deduct_credits(current_user['id'], CLASSIC_COST)
        individual_predictions = [_classic_match_prediction(m) for m in matches]
        credits_after = self._repo.get_user_credits(current_user['id'])
        return {
            'success': True,
            'individual_predictions': individual_predictions,
            'credits_remaining': credits_after,
            '_http_status': 200,
        }

    def simple_predict(self, matches: list) -> dict:
        if not matches:
            return {'success': False, 'message': '未提供比赛数据', '_http_status': 400}
        _log_user_prediction(matches)
        preds = [_simple_match_prediction(m) for m in matches]
        return {
            'success': True,
            'individual_predictions': preds,
            'message': '简化预测模式，推荐使用AI智能预测获得更准确结果',
            '_http_status': 200,
        }


def _classic_match_prediction(m: dict) -> dict:
    odds = (m.get('odds') or {}).get('hhad', {})
    ho = float(odds.get('h') or m.get('home_odds') or 2.0)
    do_ = float(odds.get('d') or m.get('draw_odds') or 3.2)
    ao = float(odds.get('a') or m.get('away_odds') or 3.5)
    home = m.get('home_team', '')
    away = m.get('away_team', '')
    league = m.get('league_code', '') or m.get('league_name', '')
    raw_h, raw_d, raw_a = 1 / max(ho, 1.01), 1 / max(do_, 1.01), 1 / max(ao, 1.01)
    total = raw_h + raw_d + raw_a
    ph, pd, pa = raw_h / total, raw_d / total, raw_a / total
    if ph >= pd and ph >= pa:
        rec = '主胜'
    elif pd >= ph and pd >= pa:
        rec = '平局'
    else:
        rec = '客胜'
    lam_h = max(-math.log(max(1 - ph, 0.01)) * 1.5, 0.3)
    lam_a = max(-math.log(max(1 - pa, 0.01)) * 1.5, 0.3)
    score_h = min(round(lam_h), 4)
    score_a = min(round(lam_a), 4)
    best_odds = ho if rec == '主胜' else (do_ if rec == '平局' else ao)
    best_prob = ph if rec == '主胜' else (pd if rec == '平局' else pa)
    return {
        'home_team': home,
        'away_team': away,
        'league': league,
        'mode': 'statistical',
        'probabilities': {'home': round(ph, 4), 'draw': round(pd, 4), 'away': round(pa, 4)},
        'recommendation': rec,
        'score_prediction': f'{score_h}-{score_a}',
        'halftime_prediction': '主胜' if ph > 0.45 else ('平局' if pd > 0.35 else '客胜'),
        'halftime_score': f'{max(score_h - 1, 0)}-{max(score_a - 1, 0)}',
        'ht_ft_combo': f'{"主胜" if ph > 0.45 else "平局"}/{"主胜" if ph > 0.4 else "平局"}',
        'top_scores': [
            {'score': f'{score_h}-{score_a}', 'prob': round(ph * 35, 1)},
            {'score': f'{score_h + 1}-{score_a}', 'prob': round(ph * 18, 1)},
            {'score': f'{score_h}-{score_a + 1}', 'prob': round(pa * 22, 1)},
            {'score': '1-1', 'prob': round(pd * 30, 1)},
            {'score': '0-0', 'prob': round(pd * 18, 1)},
        ],
        'expected_values': {
            'home': round(ph * ho - 1, 3),
            'draw': round(pd * do_ - 1, 3),
            'away': round(pa * ao - 1, 3),
        },
        'best_bet': {'label': rec, 'odds': best_odds, 'ev': round(best_prob * best_odds - 1, 3)},
    }


def _simple_match_prediction(match: dict) -> dict:
    home_odds = float(match.get('home_odds', 2.0))
    draw_odds = float(match.get('draw_odds', 3.0))
    away_odds = float(match.get('away_odds', 2.5))
    home_prob = 1 / home_odds
    draw_prob = 1 / draw_odds
    away_prob = 1 / away_odds
    total_prob = home_prob + draw_prob + away_prob
    home_prob /= total_prob
    draw_prob /= total_prob
    away_prob /= total_prob
    if home_prob > max(draw_prob, away_prob):
        rec = '主胜'
    elif draw_prob > away_prob:
        rec = '平局'
    else:
        rec = '客胜'
    return {
        'match': f"{match['home_team']} vs {match['away_team']}",
        'home_team': match['home_team'],
        'away_team': match['away_team'],
        'probabilities': {
            'home': round(home_prob, 3),
            'draw': round(draw_prob, 3),
            'away': round(away_prob, 3),
        },
        'odds': {'home': home_odds, 'draw': draw_odds, 'away': away_odds},
        'recommendation': rec,
    }


def _log_user_prediction(matches: list) -> None:
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'matches_count': len(matches),
            'matches': matches,
        }
        with open('user_predictions.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


prediction_service = PredictionService()
