# -*- coding: utf-8 -*-
"""预测回顾相关数据库查询。"""
from typing import Any, Optional


class AccuracyRepository:
    def __init__(self, db):
        self._db = db

    def fetch_overall_stats(self) -> tuple:
        """返回 (total_finished, total_predicted, correct, score_hit, avg_goal_err)。"""
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE actual_result IS NOT NULL AND ml_predicted_result IS NOT NULL
                    ) AS total_finished,
                    COUNT(*) FILTER (WHERE result_correct IS NOT NULL) AS total_predicted,
                    COUNT(*) FILTER (WHERE result_correct = true) AS correct,
                    COUNT(*) FILTER (WHERE score_correct = true) AS score_hit,
                    ROUND(AVG(goal_diff_error) FILTER (WHERE goal_diff_error IS NOT NULL), 2)
                FROM upcoming_fixtures
            """)
            return cur.fetchone()

    def fetch_league_breakdown(self) -> list:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT league_name,
                    COUNT(*) FILTER (WHERE result_correct IS NOT NULL) AS predicted,
                    COUNT(*) FILTER (WHERE result_correct = true) AS correct,
                    COUNT(*) FILTER (WHERE score_correct = true) AS score_hit
                FROM upcoming_fixtures
                WHERE actual_result IS NOT NULL AND ml_predicted_result IS NOT NULL
                GROUP BY league_name ORDER BY league_name
            """)
            return cur.fetchall()

    def fetch_finished_for_roi(self) -> list:
        """已完赛且有 ML 预测的行，供 ROI 计算。"""
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, league_name, league_code,
                       home_team, away_team, match_time,
                       actual_home_goals, actual_away_goals, actual_result,
                       ml_home_prob, ml_draw_prob, ml_away_prob,
                       ml_predicted_result, ml_recommendation,
                       predicted_home_goals, predicted_away_goals,
                       result_correct, score_correct, goal_diff_error,
                       home_odds, draw_odds, away_odds,
                       bet_odds_home, bet_odds_draw, bet_odds_away
                FROM upcoming_fixtures
                WHERE actual_result IS NOT NULL
                  AND ml_predicted_result IS NOT NULL
                  AND ml_home_prob IS NOT NULL
                  AND home_odds IS NOT NULL
                  AND draw_odds IS NOT NULL
                  AND away_odds IS NOT NULL
                ORDER BY match_time DESC
            """)
            return cur.fetchall()

    def fetch_daily_trend(self, limit: int = 14) -> list:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DATE(match_time AT TIME ZONE 'Asia/Shanghai') AS day,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE result_correct = true) AS correct
                FROM upcoming_fixtures
                WHERE actual_result IS NOT NULL AND ml_predicted_result IS NOT NULL
                GROUP BY DATE(match_time AT TIME ZONE 'Asia/Shanghai')
                ORDER BY day DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()

    @staticmethod
    def build_match_filters(
        league: Optional[str],
        result_filter: Optional[str],
    ) -> tuple[str, list[Any]]:
        conds = ['actual_result IS NOT NULL', 'ml_predicted_result IS NOT NULL']
        params: list[Any] = []
        if league:
            conds.append('league_name = %s')
            params.append(league)
        if result_filter == 'correct':
            conds.append('result_correct = true')
        elif result_filter == 'wrong':
            conds.append('result_correct = false')
        elif result_filter == 'score_hit':
            conds.append('score_correct = true')
        return ' AND '.join(conds), params

    def count_matches(self, where: str, params: list) -> int:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT COUNT(*) FROM upcoming_fixtures WHERE {where}', params)
            return cur.fetchone()[0]

    def fetch_matches(
        self,
        where: str,
        params: list,
        per_page: int,
        offset: int,
    ) -> list:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT fixture_id, league_name, league_code,
                       home_team, away_team, match_time,
                       actual_home_goals, actual_away_goals, actual_result,
                       ml_home_prob, ml_draw_prob, ml_away_prob,
                       ml_predicted_result, ml_recommendation,
                       predicted_home_goals, predicted_away_goals,
                       result_correct, score_correct, goal_diff_error,
                       home_odds, draw_odds, away_odds,
                       bet_odds_home, bet_odds_draw, bet_odds_away
                FROM upcoming_fixtures
                WHERE {where}
                ORDER BY match_time DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            return cur.fetchall()
