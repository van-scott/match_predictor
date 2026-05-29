# -*- coding: utf-8 -*-
"""智能选场单场预测业务逻辑。"""
from typing import Any, Optional

from matchpredict.db import prediction_db
from matchpredict.domain.betting import build_value_analysis
from matchpredict.repositories.fixture_repository import FixtureRepository
from matchpredict.utils.goals import interpret_odds_signal

try:
    from matchpredict.integrations.ai_predictor import AIFootballPredictor
except ImportError:
    AIFootballPredictor = None


class SmartPredictError(Exception):
    def __init__(self, message: str, status_code: int = 400, payload: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


class SmartPredictService:
    def __init__(self, db=None):
        self._db = db or prediction_db
        self._fixtures = FixtureRepository(self._db)

    def predict(
        self,
        current_user: dict,
        fixture_id: str,
        with_ai: bool = False,
        override_engine: Optional[str] = None,
        override_model: Optional[str] = None,
    ) -> dict[str, Any]:
        if not self._db:
            raise SmartPredictError('数据库未配置', 500)

        fixture_id = (fixture_id or '').strip()
        if not fixture_id:
            raise SmartPredictError('fixture_id 不能为空', 400)

        row = self._fixtures.get_upcoming_fixture(fixture_id)
        if not row:
            raise SmartPredictError('比赛不存在或已开赛', 404)

        (fix_id, _lg_code, lg_name, ht, at, mt,
         h_odds, d_odds, a_odds,
         ml_h, ml_d, ml_a, ml_rec) = row

        cost = 2 if with_ai else 1
        credits = self._db.get_user_credits(current_user['id'])
        if credits < cost:
            raise SmartPredictError(
                f'积分不足（需要{cost}积分，当前{credits}积分），请签到获取积分',
                403,
            )

        # ── D1: 置信度门控 ─────────────────────────────────────────────
        ml_confidence = 'unknown'
        value_analysis = None
        if ml_h and ml_d and ml_a:
            max_ml_prob = max(float(ml_h), float(ml_d), float(ml_a))
            if max_ml_prob >= 0.60:
                ml_confidence = 'high'
            elif max_ml_prob >= 0.45:
                ml_confidence = 'medium'
            else:
                ml_confidence = 'low'

            if h_odds and d_odds and a_odds:
                value_analysis = build_value_analysis(ml_h, ml_d, ml_a, h_odds, d_odds, a_odds)

        result: dict[str, Any] = {
            'fixture_id': fix_id,
            'league': lg_name,
            'home_team': ht,
            'away_team': at,
            'match_time': mt.isoformat() if mt else None,
            'current_odds': {
                'home': float(h_odds) if h_odds else None,
                'draw': float(d_odds) if d_odds else None,
                'away': float(a_odds) if a_odds else None,
            },
            'ml_prediction': {
                'home_prob': float(ml_h) if ml_h else None,
                'draw_prob': float(ml_d) if ml_d else None,
                'away_prob': float(ml_a) if ml_a else None,
                'recommendation': ml_rec,
                'confidence': ml_confidence,
            } if ml_h else None,
            'value_analysis': value_analysis,
            'value_edge': (
                {
                    'outcome': value_analysis['pick'],
                    'edge': value_analysis['edge'],
                    'ml_prob': max(float(ml_h), float(ml_d), float(ml_a)),
                    'is_value': True,
                } if value_analysis and value_analysis.get('is_value') else None
            ),
        }

        odds_mv = self._build_odds_movement(fix_id)
        if odds_mv:
            result['odds_movement'] = odds_mv

        if with_ai:
            self._run_ai_analysis(
                current_user, result, fix_id, ht, at, lg_name,
                h_odds, d_odds, a_odds,
                override_engine, override_model,
            )

        if not self._db.deduct_credits(current_user['id'], cost):
            raise SmartPredictError('扣积分失败，请稍后重试', 500)

        result['credits_used'] = cost
        result['credits_remaining'] = max(credits - cost, 0)
        result['success'] = True
        return result

    def _build_odds_movement(self, fixture_id: str) -> Optional[dict]:
        try:
            odds_row = self._fixtures.get_latest_odds_with_open(fixture_id)
            if not odds_row:
                return None
            cur_h, cur_d, cur_a, op_h, op_d, op_a = odds_row
            if not (cur_h and op_h):
                return None
            return {
                'home_change': round(float(cur_h) - float(op_h), 3),
                'draw_change': round(float(cur_d or 0) - float(op_d or 0), 3),
                'away_change': round(float(cur_a or 0) - float(op_a or 0), 3),
                'signal': interpret_odds_signal(float(op_h), float(cur_h)),
            }
        except Exception:
            return None

    def _run_ai_analysis(
        self,
        current_user: dict,
        result: dict,
        fix_id: str,
        ht: str,
        at: str,
        lg_name: str,
        h_odds,
        d_odds,
        a_odds,
        override_engine: Optional[str],
        override_model: Optional[str],
    ) -> None:
        can_predict = self._db.can_user_predict(
            current_user['id'],
            current_user['user_type'],
            current_user['daily_predictions_used'],
        )
        if not can_predict:
            raise SmartPredictError('今日预测次数已用完', 403)

        ai_analysis = None
        if AIFootballPredictor:
            try:
                predictor = AIFootballPredictor(
                    user_id=current_user['id'],
                    override_engine=override_engine,
                    override_model=override_model,
                )
                match_input = {
                    'match_id': fix_id,
                    'home_team': ht,
                    'away_team': at,
                    'league_name': lg_name,
                    'home_odds': float(h_odds) if h_odds else 2.0,
                    'draw_odds': float(d_odds) if d_odds else 3.2,
                    'away_odds': float(a_odds) if a_odds else 2.8,
                }
                analyses = predictor.analyze_matches([match_input], use_db=True)
                if analyses:
                    ai_analysis = analyses[0].ai_analysis
                    result['ai_recommendation'] = analyses[0].recommendation
            except SmartPredictError:
                raise
            except Exception:
                pass

        result['ai_analysis'] = ai_analysis
        self._db.increment_user_predictions(current_user['id'])


smart_predict_service = SmartPredictService()
