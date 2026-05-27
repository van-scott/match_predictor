# -*- coding: utf-8 -*-
"""预测保存与统计分析 API。"""
from flask import Blueprint, current_app, jsonify, request, session

from matchpredict.services.prediction_service import prediction_service
from matchpredict.utils.auth import get_current_user

bp = Blueprint('predictions', __name__)


def _respond(result: dict):
    code = result.pop('_http_status', 200)
    return jsonify(result), code


@bp.route('/api/save-prediction', methods=['POST'])
def save_prediction():
    try:
        current_user = get_current_user()
        result = prediction_service.save_prediction(
            current_user,
            request.get_json(),
            request.remote_addr,
        )
        if result.get('success') and '_session_user' in result:
            user = result.pop('_session_user')
            session['user_id'] = user['id']
            session['username'] = user['username']
            session.permanent = True
        return _respond(result)
    except Exception as e:
        current_app.logger.error('保存预测结果失败: %s', e)
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'}), 500


@bp.route('/api/prediction-stats', methods=['GET'])
def get_prediction_stats():
    try:
        return _respond(prediction_service.get_stats())
    except Exception as e:
        current_app.logger.error('获取统计信息失败: %s', e)
        return jsonify({'success': False, 'message': f'获取统计信息失败: {str(e)}'}), 500


@bp.route('/api/analyze/classic', methods=['POST'])
def analyze_classic():
    try:
        current_user = get_current_user()
        data = request.get_json() or {}
        return _respond(prediction_service.analyze_classic(
            current_user, data.get('matches', [])
        ))
    except Exception as e:
        current_app.logger.error('深度权重预测失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/predict', methods=['POST'])
def predict():
    try:
        data = request.get_json() or {}
        return _respond(prediction_service.simple_predict(data.get('matches', [])))
    except Exception as e:
        current_app.logger.error('预测错误: %s', e)
        return jsonify({'success': False, 'message': f'预测过程中发生错误: {str(e)}'})
