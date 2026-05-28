# -*- coding: utf-8 -*-
"""Business logic for lottery/upcoming matches."""
from matchpredict.db import prediction_db
from matchpredict.domain.team_names import TEAM_NAME_CN
from matchpredict.domain.leagues import TEAMS_DATA
from matchpredict.repositories.upcoming_repository import UpcomingRepository
from matchpredict.utils.goals import calc_predicted_goals, interpret_odds_signal


class UpcomingService:
    def __init__(self, db=None):
        self._db = db or prediction_db
        self._repo = UpcomingRepository(self._db) if self._db else None

    def get_teams_data(self):
        return {'success': True, 'teams': TEAMS_DATA, 'message': '球队数据获取成功'}

    def get_lottery_matches(self, days: int):
        days = min(max(days, 1), 14)
        rows = self._repo.fetch_lottery_matches(days)
        matches = []
        for r in rows:
            (
                fixture_id, league_code, league_name, home_team, away_team, match_time, matchday,
                home_odds, draw_odds, away_odds,
                hhad_home_odds, hhad_draw_odds, hhad_away_odds, hhad_goal_line,
                ml_home_prob, ml_draw_prob, ml_away_prob, ml_recommendation,
            ) = r
            ht_cn = TEAM_NAME_CN.get(home_team, '')
            at_cn = TEAM_NAME_CN.get(away_team, '')
            odds = {}
            if home_odds:
                odds['h'] = home_odds
            if draw_odds:
                odds['d'] = draw_odds
            if away_odds:
                odds['a'] = away_odds
            hhad_odds = {}
            if hhad_home_odds:
                hhad_odds['h'] = hhad_home_odds
            if hhad_draw_odds:
                hhad_odds['d'] = hhad_draw_odds
            if hhad_away_odds:
                hhad_odds['a'] = hhad_away_odds
            if hhad_goal_line:
                hhad_odds['goal_line'] = hhad_goal_line
            odds_payload = {}
            if odds:
                odds_payload['had'] = odds
            if hhad_odds:
                odds_payload['hhad'] = hhad_odds
            elif odds:
                # 向后兼容：历史前端默认从 odds.hhad 读取三项赔率
                odds_payload['hhad'] = odds

            matches.append({
                'fixture_id': fixture_id,
                'match_id': fixture_id,
                'league_code': league_code,
                'league_name': league_name,
                'home_team': home_team,
                'away_team': away_team,
                'home_team_cn': ht_cn or home_team,
                'away_team_cn': at_cn or away_team,
                'home_team_display': f"{ht_cn}({home_team})" if ht_cn else home_team,
                'away_team_display': f"{at_cn}({away_team})" if at_cn else away_team,
                'match_time': match_time.isoformat() if match_time else None,
                'match_date': match_time.strftime('%Y-%m-%d') if match_time else None,
                'matchday': matchday,
                'home_odds': home_odds,
                'draw_odds': draw_odds,
                'away_odds': away_odds,
                'hhad_home_odds': hhad_home_odds,
                'hhad_draw_odds': hhad_draw_odds,
                'hhad_away_odds': hhad_away_odds,
                'hhad_goal_line': hhad_goal_line,
                'odds': odds_payload,
                'ml_probs': (
                    {
                        'home': round(ml_home_prob, 4),
                        'draw': round(ml_draw_prob, 4),
                        'away': round(ml_away_prob, 4),
                    } if ml_home_prob else None
                ),
                'ml_recommendation': ml_recommendation,
            })
        return {'success': True, 'matches': matches, 'count': len(matches), 'source': 'upcoming_fixtures'}

    def get_upcoming_matches(self, days: int, page: int, per_page: int, league: str | None = None):
        days = min(days, 30)
        page = max(page, 1)
        per_page = min(per_page, 50)
        total = self._repo.count_upcoming_matches(days, league)
        rows = self._repo.fetch_upcoming_matches(days, page, per_page, league)
        matches = []
        for r in rows:
            (
                fix_id, lg_code, lg_name, ht, at, mt, matchday, h_odds, d_odds, a_odds,
                ml_h, ml_d, ml_a, ml_rec, open_h, open_d, open_a, pred_hg, pred_ag,
            ) = r
            odds_movement = {}
            if h_odds and open_h and float(open_h) > 0:
                odds_movement = {
                    'home_change': round(float(h_odds) - float(open_h), 3),
                    'draw_change': round(float(d_odds or 0) - float(open_d or 0), 3) if d_odds and open_d else 0,
                    'away_change': round(float(a_odds or 0) - float(open_a or 0), 3) if a_odds and open_a else 0,
                    'signal': interpret_odds_signal(float(open_h), float(h_odds)),
                }

            ht_cn = TEAM_NAME_CN.get(ht, '')
            at_cn = TEAM_NAME_CN.get(at, '')
            if ml_h:
                ml_pred = {
                    'home_prob': round(float(ml_h), 4),
                    'draw_prob': round(float(ml_d), 4),
                    'away_prob': round(float(ml_a), 4),
                    'recommendation': ml_rec,
                    'source': 'ml',
                }
            elif h_odds and d_odds and a_odds:
                rh, rd, ra = 1 / float(h_odds), 1 / float(d_odds), 1 / float(a_odds)
                tot = rh + rd + ra
                ph2, pd2, pa2 = round(rh / tot, 4), round(rd / tot, 4), round(ra / tot, 4)
                best_lbl = '主胜' if ph2 >= pd2 and ph2 >= pa2 else ('平局' if pd2 >= pa2 else '客胜')
                best_p = max(ph2, pd2, pa2)
                ml_pred = {
                    'home_prob': ph2, 'draw_prob': pd2, 'away_prob': pa2,
                    'recommendation': f'赔率推算{best_lbl}（{best_p*100:.1f}%）',
                    'source': 'odds_fallback',
                }
            else:
                ml_pred = None

            pg_home, pg_away = calc_predicted_goals(pred_hg, pred_ag, ml_pred, h_odds, d_odds, a_odds)
            matches.append({
                'fixture_id': fix_id,
                'league': lg_name,
                'league_code': lg_code,
                'home_team': ht,
                'away_team': at,
                'home_team_cn': ht_cn or ht,
                'away_team_cn': at_cn or at,
                'home_team_display': f"{ht_cn}({ht})" if ht_cn else ht,
                'away_team_display': f"{at_cn}({at})" if at_cn else at,
                'match_time': mt.isoformat() if mt else None,
                'matchday': matchday,
                'current_odds': {
                    'home': float(h_odds) if h_odds else None,
                    'draw': float(d_odds) if d_odds else None,
                    'away': float(a_odds) if a_odds else None,
                },
                'open_odds': {
                    'home': float(open_h) if open_h else None,
                    'draw': float(open_d) if open_d else None,
                    'away': float(open_a) if open_a else None,
                },
                'odds_movement': odds_movement,
                'ml_prediction': ml_pred,
                'predicted_home_goals': pg_home,
                'predicted_away_goals': pg_away,
            })
        return {'success': True, 'total': total, 'page': page, 'per_page': per_page, 'matches': matches}

    def get_sync_status(self):
        row = self._repo.fetch_sync_status()
        last_sync = None
        if row and row[0]:
            last_sync = row[0].strftime('%m-%d %H:%M')
        return {
            'success': True,
            'last_sync_time': last_sync,
            'message': f'上次同步: {last_sync}' if last_sync else '暂无同步记录',
        }

    def get_available_leagues(self):
        rows = self._repo.fetch_available_leagues()
        leagues = [{'name': r[0], 'code': r[1], 'upcoming_matches': r[2]} for r in rows]
        return {'success': True, 'leagues': leagues}


upcoming_service = UpcomingService()
