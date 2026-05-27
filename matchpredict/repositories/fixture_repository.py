# -*- coding: utf-8 -*-
"""赛程与赔率数据库查询。"""
from typing import Any, Optional


class FixtureRepository:
    def __init__(self, db):
        self._db = db

    def get_upcoming_fixture(self, fixture_id: str) -> Optional[tuple]:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, league_code, league_name,
                       home_team, away_team, match_time,
                       home_odds, draw_odds, away_odds,
                       ml_home_prob, ml_draw_prob, ml_away_prob, ml_recommendation
                FROM upcoming_fixtures
                WHERE fixture_id = %s
            """, (fixture_id,))
            return cur.fetchone()

    def get_latest_odds_with_open(self, fixture_id: str) -> Optional[tuple]:
        """返回 (cur_h, cur_d, cur_a, open_h, open_d, open_a)。"""
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT home_odds, draw_odds, away_odds,
                       open_home_odds, open_draw_odds, open_away_odds
                FROM match_odds
                WHERE match_id = %s
                ORDER BY updated_at DESC LIMIT 1
            """, (fixture_id,))
            return cur.fetchone()
