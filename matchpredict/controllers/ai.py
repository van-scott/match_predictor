# -*- coding: utf-8 -*-
"""HTTP 控制器 — 仅处理请求/响应，业务逻辑在 services 层。"""
from flask import Blueprint, request, jsonify, current_app

from matchpredict.db import prediction_db
from matchpredict.services.ai_prediction_service import ai_prediction_service
from matchpredict.utils.auth import get_current_user

bp = Blueprint("ai", __name__)

@bp.route('/api/ai/predict', methods=['POST'])
def ai_predict():
    """AI大模型预测。"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录', 'error_code': 'NOT_LOGGED_IN'}), 401
        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库服务不可用'}), 500

        data = request.get_json() or {}
        matches = data.get('matches', [])
        if not matches:
            return jsonify({'success': False, 'message': '未提供比赛数据'}), 400

        result = ai_prediction_service.predict_matches(
            current_user=current_user,
            matches=matches,
            override_engine=data.get('override_engine') or None,
            override_model=data.get('override_model') or None,
        )
        status = result.pop('_status', 200)
        return jsonify(result), status

    except Exception as e:
        current_app.logger.error(f'AI预测失败: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500



@bp.route('/api/ai/save', methods=['POST'])
def ai_save():
    """保存 AI 分析结果到 match_predictions 表（调用 service）。"""
    from matchpredict.services.ai_prediction_service import ai_prediction_service

    try:
        current_user = get_current_user()
        data = request.get_json() or {}
        results = data.get('results', [])
        # 装上 user_ip 方便 service 传下去
        for r in results or []:
            r.setdefault('user_ip', request.remote_addr)
        result = ai_prediction_service.save_results(current_user, results)
        status = result.pop('_status', 200)
        return jsonify(result), status
    except Exception as e:
        current_app.logger.error("保存AI分析失败: %s", e, exc_info=True)
        return jsonify({'success': True, 'saved': 0}), 200

@bp.route('/api/ai/batch-predict', methods=['POST'])
def ai_batch_predict():
    """AI批量预测"""
    try:
        data = request.json
        matches = data.get('matches', [])
        
        if not matches:
            return jsonify({
                'success': False,
                'message': '未提供比赛数据'
            })
        
        # 调用AI预测
        return ai_predict()
        
    except Exception as e:
        current_app.logger.error(f"AI批量预测错误: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量预测失败: {str(e)}'
        })

@bp.route('/api/smart-predict', methods=['POST'])
def smart_predict():
    """单场智能预测：ML 概率 + 赔率变动 + 可选 AI 深度分析。"""
    from matchpredict.services.smart_predict_service import (
        SmartPredictError,
        smart_predict_service,
    )

    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        data = request.get_json() or {}
        result = smart_predict_service.predict(
            current_user=current_user,
            fixture_id=data.get('fixture_id', ''),
            with_ai=bool(data.get('with_ai', False)),
            override_engine=data.get('override_engine') or None,
            override_model=data.get('override_model') or None,
        )
        return jsonify(result)
    except SmartPredictError as e:
        body = {'success': False, 'message': e.message, **e.payload}
        return jsonify(body), e.status_code
    except Exception as e:
        current_app.logger.error('智能预测失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


