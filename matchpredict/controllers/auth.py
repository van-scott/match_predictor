# -*- coding: utf-8 -*-
"""认证与用户账户 API。"""
from flask import Blueprint, current_app, jsonify, request, session

from matchpredict.data import prediction_db
from matchpredict.services.auth_service import auth_service
from matchpredict.utils.auth import get_current_user

bp = Blueprint('auth', __name__)


def _respond(result: dict):
    code = result.pop('_http_status', 200)
    return jsonify(result), code


@bp.route('/api/register', methods=['POST', 'OPTIONS'])
def register():
    try:
        data = request.get_json() or {}
        result = auth_service.register(
            data.get('username', '').strip(),
            data.get('email', '').strip(),
            data.get('password', ''),
        )
        return _respond(result)
    except Exception as e:
        current_app.logger.error('用户注册失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '注册失败，请稍后重试'}), 500


@bp.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    try:
        data = request.get_json() or {}
        result = auth_service.login(
            data.get('username', '').strip(),
            data.get('password', ''),
        )
        if result.get('success') and '_session_user' in result:
            user = result.pop('_session_user')
            session['user_id'] = user['id']
            session['username'] = user['username']
            session.permanent = True
        return _respond(result)
    except Exception as e:
        current_app.logger.error('用户登录失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '登录失败，请稍后重试'}), 500


@bp.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': '已安全退出'})


@bp.route('/api/user/info', methods=['GET'])
def get_user_info():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '未登录'}), 401
        result = auth_service.get_user_info(current_user)
        if result.get('_http_status') == 401:
            session.clear()
        return _respond(result)
    except Exception as e:
        current_app.logger.error('获取用户信息失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '获取用户信息失败'}), 500


@bp.route('/api/user/can-predict', methods=['GET'])
def can_user_predict_api():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '未登录', 'can_predict': False}), 401
        result = auth_service.can_predict(current_user)
        if result.get('_http_status') == 401:
            session.clear()
        return _respond(result)
    except Exception as e:
        current_app.logger.error('检查预测权限失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '检查失败'}), 500


@bp.route('/api/user/credits', methods=['GET'])
def get_user_credits():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'credits': 0, 'message': '未登录'}), 401
    return _respond(auth_service.get_credits(current_user))


@bp.route('/api/user/checkin', methods=['POST'])
def user_checkin():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return _respond(auth_service.checkin(current_user))


@bp.route('/api/user/ai-config', methods=['GET'])
def get_user_ai_config_api():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return _respond(auth_service.get_ai_config(current_user))


@bp.route('/api/user/save-ai-config', methods=['POST'])
def save_user_ai_config_api():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return _respond(auth_service.save_ai_config(current_user, request.get_json() or {}))


@bp.route('/api/user/test-ai-config', methods=['POST'])
def test_user_ai_config_api():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    try:
        return _respond(auth_service.test_ai_config(current_user, request.get_json() or {}))
    except Exception as e:
        current_app.logger.warning('AI 配置测试失败: %s', e)
        err_msg = str(e)
        if 'FileNotFoundError' in err_msg or '未找到命令行' in err_msg or 'No such file' in err_msg:
            return jsonify({
                'success': False,
                'message': '连接测试失败：未在本地系统找到指定的 CLI 可执行命令或路径。请确保对应工具已安装，并已正确配置绝对路径。',
            })
        return jsonify({'success': False, 'message': f'连接测试失败：{err_msg}'})


@bp.route('/api/user/login-cli', methods=['POST'])
def login_cli_api():
    current_user = get_current_user()
    if not current_user or not prediction_db:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    data = request.get_json() or {}
    engine_type = data.get('ai_engine_type', '').strip()
    cli_path = data.get('cli_path', '').strip()
    if not engine_type or not cli_path:
        return jsonify({'success': False, 'message': 'CLI 类型与命令路径不能为空'}), 400
    api_key = auth_service.resolve_masked_api_key(
        current_user['id'], data.get('ai_api_key', '').strip()
    )
    return _respond(auth_service.login_cli_sync(
        current_user,
        engine_type,
        cli_path,
        api_key,
        data.get('ai_api_url', '').strip(),
        data.get('ai_model', '').strip(),
    ))


@bp.route('/api/user/start-cli-login', methods=['POST'])
def start_cli_login_api():
    current_user = get_current_user()
    if not current_user or not prediction_db:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    data = request.get_json() or {}
    return _respond(auth_service.start_cli_login(
        data.get('ai_engine_type', '').strip(),
        data.get('cli_path', '').strip(),
    ))


@bp.route('/api/user/cli-login-poll/<job_id>', methods=['GET'])
def cli_login_poll_api(job_id: str):
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return _respond(auth_service.poll_cli_login(job_id))


@bp.route('/api/user/cli-logout', methods=['POST'])
def cli_logout_api():
    current_user = get_current_user()
    if not current_user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    data = request.get_json() or {}
    return _respond(auth_service.cli_logout(
        data.get('cli_path', '').strip(),
        data.get('ai_engine_type', '').strip(),
    ))


@bp.route('/api/user/detect-cli-models', methods=['POST'])
def detect_cli_models_api():
    current_user = get_current_user()
    if not current_user or not prediction_db:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    data = request.get_json() or {}
    return _respond(auth_service.detect_cli_models(
        current_user,
        data.get('cli_path', '').strip(),
        data.get('ai_engine_type', '').strip(),
    ))
