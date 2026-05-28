# -*- coding: utf-8 -*-
"""管理员 API 控制器。"""
import os

import requests
from flask import Blueprint, jsonify, request, current_app

from matchpredict.db import prediction_db
from matchpredict.utils.auth import get_current_user

bp = Blueprint('admin', __name__)


@bp.route('/api/admin/init-db', methods=['POST'])
def admin_init_db():
    try:
        data = request.get_json() or {}
        secret = data.get('secret') or request.headers.get('X-Admin-Secret', '')
        expected = os.environ.get('ADMIN_SECRET', '')

        if not expected:
            return jsonify({
                'success': False,
                'message': '服务器未配置 ADMIN_SECRET 环境变量，禁止操作',
            }), 403

        if secret != expected:
            current_app.logger.warning('非法的 init-db 请求，来源 IP: %s', request.remote_addr)
            return jsonify({'success': False, 'message': '密钥错误，拒绝访问'}), 403

        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库未连接'}), 500

        prediction_db.init_tables()
        prediction_db.ensure_credits_columns()
        prediction_db.ensure_ai_config_columns()

        admin_result = prediction_db.init_admin(
            username=data.get('username', 'admin'),
            email=data.get('email', 'admin@matchpro.com'),
            password=data.get('password', 'admin888'),
        )

        current_app.logger.info('数据库初始化完成，超管: %s', admin_result)
        return jsonify({
            'success': True,
            'message': '数据库初始化完成',
            'tables': ['users', 'match_predictions', 'daily_matches'],
            'admin': admin_result,
        })

    except Exception as e:
        current_app.logger.error('数据库初始化失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/admin/detect-models', methods=['POST'])
def admin_detect_models():
    current_user = get_current_user()
    if not current_user or current_user.get('user_type') != 'admin':
        return jsonify({'success': False, 'message': '需要管理员权限'}), 403

    data = request.get_json() or {}
    url = data.get('url', '').strip().rstrip('/')
    key = data.get('key', '').strip()

    if not url or not key:
        return jsonify({'success': False, 'message': '请提供 URL 和 Key'})

    try:
        resp = requests.get(
            f'{url}/models',
            headers={'Authorization': f'Bearer {key}'},
            timeout=15,
        )
        if resp.status_code == 200:
            body = resp.json()
            models = []
            if 'data' in body:
                for m in body['data'][:50]:
                    models.append({'id': m.get('id', ''), 'name': m.get('name', m.get('id', ''))})
            elif isinstance(body, list):
                for m in body[:50]:
                    models.append({'id': m.get('id', ''), 'name': m.get('name', '')})
            if models:
                return jsonify({'success': True, 'models': models})
            return jsonify({'success': False, 'message': '响应格式无法解析，但连接成功'})
        return jsonify({'success': False, 'message': f'HTTP {resp.status_code}: {resp.text[:200]}'})
    except requests.Timeout:
        return jsonify({'success': False, 'message': '连接超时'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@bp.route('/api/admin/save-ai-config', methods=['POST'])
def admin_save_ai_config():
    current_user = get_current_user()
    if not current_user or current_user.get('user_type') != 'admin':
        return jsonify({'success': False, 'message': '需要管理员权限'}), 403

    data = request.get_json() or {}
    url = data.get('url', '').strip()
    key = data.get('key', '').strip()
    model = data.get('model', '').strip()

    if not url or not key or not model:
        return jsonify({'success': False, 'message': '所有字段必填'})

    try:
        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()
            for k, v in [('ai_api_url', url), ('ai_api_key', key), ('ai_model', model)]:
                cur.execute("""
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                """, (k, v))
        return jsonify({'success': True, 'message': '配置已保存'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
