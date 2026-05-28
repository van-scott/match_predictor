# -*- coding: utf-8 -*-
"""管理员配置业务逻辑。"""
import os

from matchpredict.db import prediction_db


def get_ai_config() -> dict:
    config = {
        'ai_api_url': (
            os.environ.get('GEMINI_BASE_URL')
            or os.environ.get('GEMINI_API_URL')
            or 'https://openrouter.ai/api/v1'
        ),
        'ai_api_key': '',
        'ai_model': os.environ.get('GEMINI_MODEL', 'openrouter/owl-alpha'),
    }
    if not prediction_db:
        return config
    try:
        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value FROM system_config WHERE key IN ('ai_api_url','ai_api_key','ai_model')"
            )
            for k, v in cur.fetchall():
                if v:
                    config[k] = v
    except Exception:
        pass
    return config
