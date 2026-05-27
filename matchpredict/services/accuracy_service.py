# -*- coding: utf-8 -*-
"""预测回顾业务逻辑。"""
from typing import Any, Optional

from matchpredict.data import prediction_db
from matchpredict.domain.team_names import TEAM_NAME_CN
from matchpredict.repositories.accuracy_repository import AccuracyRepository

RESULT_LABEL = {'H': '主胜', 'D': '平局', 'A': '客胜'}


class AccuracyService:
    def __init__(self, db=None):
        self._repo = AccuracyRepository(db or prediction_db)

    def get_summary(self) -> dict:
        total_fin, total_pred, correct, score_hit, avg_err = self._repo.fetch_overall_stats()
        accuracy = round(correct / total_pred * 100, 1) if total_pred > 0 else 0

        league_stats = []
        for lg, pred, corr, sh in self._repo.fetch_league_breakdown():
            if pred > 0:
                league_stats.append({
                    'league': lg,
                    'total': pred,
                    'correct': corr,
                    'score_hit': sh or 0,
                    'accuracy': round(corr / pred * 100, 1),
                })

        trend = [
            {
                'date': str(day),
                'total': total,
                'correct': corr,
                'accuracy': round(corr / total * 100, 1) if total > 0 else 0,
            }
            for day, total, corr in self._repo.fetch_daily_trend()
        ]

        return {
            'success': True,
            'summary': {
                'total_finished': total_fin or 0,
                'total_predicted': total_pred or 0,
                'correct': correct or 0,
                'score_hit': score_hit or 0,
                'accuracy': accuracy,
                'avg_goal_error': float(avg_err) if avg_err else None,
            },
            'league_stats': league_stats,
            'trend': trend,
        }

    def get_matches(
        self,
        league: Optional[str] = None,
        result_filter: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        page = max(page, 1)
        per_page = min(per_page, 50)
        offset = (page - 1) * per_page

        where, params = self._repo.build_match_filters(league, result_filter)
        total = self._repo.count_matches(where, params)
        rows = self._repo.fetch_matches(where, params, per_page, offset)

        matches = [self._format_match_row(r) for r in rows]
        return {
            'success': True,
            'total': total,
            'page': page,
            'per_page': per_page,
            'matches': matches,
        }

    @staticmethod
    def _format_match_row(r: tuple) -> dict[str, Any]:
        (fid, lg, lc, ht, at, mt,
         ahg, aag, ar,
         mlh, mld, mla,
         mlpr, mlrec,
         phg, pag,
         rc, sc, gde,
         ho, do_, ao) = r

        ht_cn = TEAM_NAME_CN.get(ht, '')
        at_cn = TEAM_NAME_CN.get(at, '')

        return {
            'fixture_id': fid,
            'league': lg,
            'league_code': lc,
            'home_team': ht,
            'away_team': at,
            'home_team_cn': ht_cn or ht,
            'away_team_cn': at_cn or at,
            'match_time': mt.isoformat() if mt else None,
            'actual_score': f'{ahg}-{aag}' if ahg is not None else None,
            'actual_home_goals': ahg,
            'actual_away_goals': aag,
            'actual_result': RESULT_LABEL.get(ar, ar),
            'actual_result_code': ar,
            'predicted_score': f'{phg}-{pag}' if phg is not None else None,
            'predicted_home_goals': phg,
            'predicted_away_goals': pag,
            'predicted_result': RESULT_LABEL.get(mlpr, mlpr) if mlpr else None,
            'predicted_result_code': mlpr,
            'ml_recommendation': mlrec,
            'ml_probs': {
                'home': round(float(mlh), 4) if mlh else None,
                'draw': round(float(mld), 4) if mld else None,
                'away': round(float(mla), 4) if mla else None,
            } if mlh else None,
            'result_correct': rc,
            'score_correct': sc,
            'goal_diff_error': gde,
            'odds': {
                'home': float(ho) if ho else None,
                'draw': float(do_) if do_ else None,
                'away': float(ao) if ao else None,
            },
        }


accuracy_service = AccuracyService()
