# -*- coding: utf-8 -*-
"""预测回顾 API 控制器。"""
from flask import Blueprint, jsonify, request, current_app

from matchpredict.data import prediction_db
from matchpredict.services.accuracy_service import accuracy_service
from matchpredict.services.eval_service import eval_service

bp = Blueprint('accuracy', __name__)


@bp.route('/api/accuracy/summary', methods=['GET'])
def accuracy_summary():
    try:
        if not prediction_db:
            return jsonify({'success': False}), 500
        return jsonify(accuracy_service.get_summary())
    except Exception as e:
        current_app.logger.error('准确率统计失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/accuracy/eval', methods=['GET'])
def accuracy_eval():
    """ML 模型评估快照：F1/Brier/校准曲线 + 历史趋势。"""
    try:
        if not prediction_db:
            return jsonify({'success': False}), 500
        days = int(request.args.get('days', 30))
        refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
        return jsonify(eval_service.get_dashboard(days=days, refresh=refresh))
    except Exception as e:
        current_app.logger.error('评估快照失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/accuracy/matches', methods=['GET'])
def accuracy_matches():
    try:
        if not prediction_db:
            return jsonify({'success': False}), 500
        data = accuracy_service.get_matches(
            league=request.args.get('league'),
            result_filter=request.args.get('result'),
            page=int(request.args.get('page', 1)),
            per_page=int(request.args.get('per_page', 20)),
        )
        return jsonify(data)
    except Exception as e:
        current_app.logger.error('预测对比列表失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500
