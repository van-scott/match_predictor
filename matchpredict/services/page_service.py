# -*- coding: utf-8 -*-
"""页面渲染所需的业务数据组装。"""
from datetime import date

from matchpredict.data import prediction_db
from matchpredict.domain.team_names import TEAM_NAME_CN
from matchpredict.services.admin_service import get_ai_config


def build_profile_context(current_user: dict) -> dict:
    """个人中心页面上下文（不含 HTML 字符串拼接）。"""
    user_ai_config = {
        'ai_engine_type': 'api_key',
        'ai_api_url': '',
        'ai_api_key': '',
        'ai_model': '',
        'cli_path_kiro': 'kiro',
        'cli_path_antigravity': 'antigravity',
        'cli_path_cursor': 'cursor',
    }
    credits = 0
    total_predictions = current_user.get('total_predictions', 0)
    history_items = []
    analyses_json = []
    already_checked = False

    if prediction_db:
        try:
            user_ai_config = prediction_db.get_user_ai_config(current_user['id'])
            if user_ai_config.get('ai_engine_type') == 'system':
                user_ai_config['ai_engine_type'] = 'api_key'
        except Exception:
            pass

        try:
            credits = prediction_db.get_user_credits(current_user['id'])
            already_checked = current_user.get('last_checkin_date') == date.today()

            with prediction_db.get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT home_team, away_team, league_name, match_time,
                           home_odds, draw_odds, away_odds, prediction_mode, created_at, ai_analysis
                    FROM match_predictions
                    WHERE user_id = %s
                    ORDER BY created_at DESC LIMIT 50
                """, (current_user['id'],))
                rows = cur.fetchall()

                for idx, r in enumerate(rows):
                    ht, at, league, _mt, ho, do_, ao, mode, created, analysis = r
                    ht_cn = TEAM_NAME_CN.get(ht, ht) if ht else '-'
                    at_cn = TEAM_NAME_CN.get(at, at) if at else '-'
                    time_str = created.strftime('%Y-%m-%d %H:%M') if created else '-'
                    history_items.append({
                        'idx': idx,
                        'time_str': time_str,
                        'mode': mode or '?',
                        'is_ai': mode == 'ai',
                        'home_cn': ht_cn,
                        'away_cn': at_cn,
                        'league': league or '-',
                        'home_odds': float(ho) if ho else None,
                        'draw_odds': float(do_) if do_ else None,
                        'away_odds': float(ao) if ao else None,
                    })
                    analyses_json.append({
                        'home': ht_cn,
                        'away': at_cn,
                        'league': league or '',
                        'time': time_str,
                        'analysis': analysis or '',
                    })
        except Exception:
            pass

    first_char = current_user['username'][0] if current_user.get('username') else '?'

    return {
        'current_user': current_user,
        'credits': credits,
        'total_predictions': total_predictions,
        'history_count': len(history_items),
        'history_items': history_items,
        'already_checked': already_checked,
        'config': user_ai_config,
        'first_char': first_char,
        'analyses_json': analyses_json,
    }


def build_index_context(current_user) -> dict:
    import os
    from datetime import date

    already_checked = False
    if current_user:
        already_checked = current_user.get('last_checkin_date') == date.today()

    return {
        'gemini_api_key': os.environ.get('GEMINI_API_KEY', ''),
        'gemini_model': os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-06-17'),
        'current_user': current_user,
        'already_checked': already_checked,
    }


def build_admin_ai_config_context(current_user) -> dict:
    config = get_ai_config()
    masked_key = config['ai_api_key']
    if masked_key and len(masked_key) > 10:
        masked_key = masked_key[:6] + '***' + masked_key[-4:]
    return {'config': config, 'masked_key': masked_key}
