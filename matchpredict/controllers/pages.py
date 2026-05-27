# -*- coding: utf-8 -*-
"""页面路由控制器。"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, session, current_app

from matchpredict.extensions import lottery_spider, ai_predictor
from matchpredict.services.page_service import (
    build_admin_ai_config_context,
    build_index_context,
    build_profile_context,
)
from matchpredict.utils.auth import get_current_user

bp = Blueprint('pages', __name__)


@bp.route('/api/session/debug')
def session_debug():
    return jsonify({
        'logged_in': 'user_id' in session,
        'user_id': session.get('user_id'),
        'username': session.get('username'),
    })


@bp.route('/')
def index():
    try:
        ctx = build_index_context(get_current_user())
        return render_template('index.html', **ctx)
    except Exception as e:
        current_app.logger.error('渲染主页失败: %s', e)
        return f'页面加载错误: {str(e)}', 500


@bp.route('/profile')
def profile():
    current_user = get_current_user()
    if not current_user:
        return render_template('errors/redirect_home.html'), 302
    try:
        ctx = build_profile_context(current_user)
        return render_template('profile.html', **ctx)
    except Exception as e:
        current_app.logger.error('渲染个人中心失败: %s', e)
        return f'页面加载错误: {str(e)}', 500


@bp.route('/test')
def test():
    return jsonify({
        'status': 'ok',
        'message': '服务正常运行',
        'lottery_spider': lottery_spider is not None,
        'ai_predictor': ai_predictor is not None,
        'timestamp': datetime.now().isoformat(),
    })


@bp.route('/admin/ai-config')
def admin_ai_config():
    current_user = get_current_user()
    if not current_user or current_user.get('user_type') != 'admin':
        return render_template('errors/admin_required.html'), 403
    ctx = build_admin_ai_config_context(current_user)
    return render_template('admin_ai_config.html', **ctx)
