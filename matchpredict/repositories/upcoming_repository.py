# -*- coding: utf-8 -*-
"""Upcoming/Lottery related database queries."""


class UpcomingRepository:
    def __init__(self, db):
        self._db = db

    def fetch_lottery_matches(self, days: int) -> list:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT fixture_id, league_code, league_name,
                       home_team, away_team, match_time, matchday,
                       home_odds, draw_odds, away_odds,
                       hhad_home_odds, hhad_draw_odds, hhad_away_odds, hhad_goal_line,
                       ml_home_prob, ml_draw_prob, ml_away_prob, ml_recommendation
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED','TIMED')
                  AND match_time > NOW()
                  AND match_time < NOW() + (%s || ' days')::interval
                ORDER BY match_time ASC
                LIMIT 80
                """,
                (days,),
            )
            return cur.fetchall()

    def count_upcoming_matches(self, days: int, league: str | None = None) -> int:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            if league:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM upcoming_fixtures
                    WHERE status IN ('SCHEDULED','TIMED')
                      AND match_time > NOW()
                      AND match_time < NOW() + (%s || ' days')::interval
                      AND league_name = %s
                    """,
                    (days, league),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM upcoming_fixtures
                    WHERE status IN ('SCHEDULED','TIMED')
                      AND match_time > NOW()
                      AND match_time < NOW() + (%s || ' days')::interval
                    """,
                    (days,),
                )
            return cur.fetchone()[0]

    def fetch_upcoming_matches(self, days: int, page: int, per_page: int, league: str | None = None) -> list:
        offset = (page - 1) * per_page
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            if league:
                cur.execute(
                    """
                    SELECT uf.fixture_id, uf.league_code, uf.league_name,
                           uf.home_team, uf.away_team, uf.match_time, uf.matchday,
                           uf.home_odds, uf.draw_odds, uf.away_odds,
                           uf.hhad_home_odds, uf.hhad_draw_odds, uf.hhad_away_odds, uf.hhad_goal_line,
                           uf.ml_home_prob, uf.ml_draw_prob, uf.ml_away_prob,
                           uf.ml_recommendation,
                           mo.open_home_odds, mo.open_draw_odds, mo.open_away_odds,
                           uf.predicted_home_goals, uf.predicted_away_goals
                    FROM upcoming_fixtures uf
                    LEFT JOIN match_odds mo ON (
                        mo.match_id = uf.fixture_id
                        OR (mo.home_team = uf.home_team AND mo.away_team = uf.away_team
                            AND mo.match_date = uf.match_time::date)
                    )
                    WHERE uf.status IN ('SCHEDULED','TIMED')
                      AND uf.match_time > NOW()
                      AND uf.match_time < NOW() + (%s || ' days')::interval
                      AND uf.league_name = %s
                    ORDER BY uf.match_time ASC
                    LIMIT %s OFFSET %s
                    """,
                    (days, league, per_page, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT uf.fixture_id, uf.league_code, uf.league_name,
                           uf.home_team, uf.away_team, uf.match_time, uf.matchday,
                           uf.home_odds, uf.draw_odds, uf.away_odds,
                           uf.hhad_home_odds, uf.hhad_draw_odds, uf.hhad_away_odds, uf.hhad_goal_line,
                           uf.ml_home_prob, uf.ml_draw_prob, uf.ml_away_prob,
                           uf.ml_recommendation,
                           mo.open_home_odds, mo.open_draw_odds, mo.open_away_odds,
                           uf.predicted_home_goals, uf.predicted_away_goals
                    FROM upcoming_fixtures uf
                    LEFT JOIN match_odds mo ON (
                        mo.match_id = uf.fixture_id
                        OR (mo.home_team = uf.home_team AND mo.away_team = uf.away_team
                            AND mo.match_date = uf.match_time::date)
                    )
                    WHERE uf.status IN ('SCHEDULED','TIMED')
                      AND uf.match_time > NOW()
                      AND uf.match_time < NOW() + (%s || ' days')::interval
                    ORDER BY uf.match_time ASC
                    LIMIT %s OFFSET %s
                    """,
                    (days, per_page, offset),
                )
            return cur.fetchall()

    def fetch_sync_status(self):
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(updated_at) FROM upcoming_fixtures")
            return cur.fetchone()

    def fetch_available_leagues(self):
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT league_name, league_code, COUNT(*) as cnt
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW()
                GROUP BY league_name, league_code
                ORDER BY cnt DESC
                """
            )
            return cur.fetchall()
