# -*- coding: utf-8 -*-
"""HTTP 控制器 — 仅处理请求/响应，业务逻辑在 services 层。"""
import os

from flask import Blueprint, request, jsonify, current_app

from matchpredict.db import prediction_db
from matchpredict.extensions import lottery_spider
from matchpredict.services.upcoming_service import upcoming_service
from matchpredict.services.scheduler_service import run_sync_upcoming_once
from matchpredict.utils.auth import get_current_user

bp = Blueprint("matches", __name__)

@bp.route('/api/teams')
def get_teams():
    """获取球队数据（服务层）。"""
    try:
        return jsonify(upcoming_service.get_teams_data())
    except Exception as e:
        current_app.logger.error("获取球队数据失败: %s", e)
        return jsonify({'success': False, 'error': str(e), 'message': '获取球队数据失败'}), 500

@bp.route('/api/lottery/matches')
def get_lottery_matches():
    """获取未开赛比赛（服务层）。"""
    try:
        days = request.args.get('days', 7, type=int)
        if not prediction_db:
            return jsonify({'success': False, 'error': '数据库未配置'}), 500
        result = upcoming_service.get_lottery_matches(days)
        if not result.get('matches'):
            return jsonify({
                'success': False,
                'error': '暂无比赛数据',
                'message': f'未来{days}天暂无未开赛比赛，请运行 make sync-upcoming',
            }), 404
        return jsonify(result)
    except Exception as e:
        current_app.logger.error("获取比赛数据失败: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lottery/refresh', methods=['POST'])
def refresh_lottery_data():
    """刷新彩票数据"""
    try:
        data = request.json
        days = data.get('days', 3)
        
        if not lottery_spider:
            return jsonify({
                'success': False,
                'message': '彩票API未初始化'
            })
        
        matches = lottery_spider.get_formatted_matches(days)
        
        return jsonify({
            'success': True,
            'matches': matches,
            'count': len(matches)
        })
        
    except Exception as e:
        current_app.logger.error(f"刷新彩票数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'刷新数据失败: {str(e)}'
        })

@bp.route('/api/sync/status')
def sync_status():
    """赛程同步状态 - 返回最后一次同步时间"""
    try:
        return jsonify(upcoming_service.get_sync_status())
    except Exception as e:
        return jsonify({'success': True, 'last_sync_time': None, 'message': '状态获取失败'})


@bp.route('/api/upcoming-matches', methods=['GET'])
def get_upcoming_matches():
    """
    获取即将开赛的比赛列表（已有 ML 预测概率）。
    支持按联赛过滤、分页。
    Query params:
      league   - 联赛名称（如 英超）
      days     - 未来N天（默认14）
      page     - 页码（默认1）
      per_page - 每页条数（默认20，最大50）
    """
    try:
        league = request.args.get('league')
        days = int(request.args.get('days', 14))
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库未配置'}), 500
        return jsonify(upcoming_service.get_upcoming_matches(days, page, per_page, league))
    except Exception as e:
        current_app.logger.error(f"获取未开赛比赛失败: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/sync-upcoming', methods=['POST'])
def trigger_sync_upcoming():
    """管理员接口：手动触发赛程同步（仅 admin 可用）"""
    try:
        current_user = get_current_user()
        if not current_user or current_user.get('user_type') != 'admin':
            return jsonify({'success': False, 'message': '仅管理员可操作'}), 403

        env = {
            **os.environ,
            'DB_HOST': os.environ.get('DB_HOST', ''),
            'DB_PORT': os.environ.get('DB_PORT', '5432'),
            'DB_NAME': os.environ.get('DB_NAME', ''),
            'DB_USER': os.environ.get('DB_USER', ''),
            'DB_PASS': os.environ.get('DB_PASS', ''),
        }
        result = run_sync_upcoming_once(env)
        return jsonify({
            'success':   result.returncode == 0,
            'stdout':    result.stdout[-2000:],
            'stderr':    result.stderr[-500:] if result.returncode != 0 else '',
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



# ═══════════════════════════════════════════════════════════════════════════
# 预测战绩对比 API
# ═══════════════════════════════════════════════════════════════════════════

@bp.route('/api/leagues', methods=['GET'])
def get_available_leagues():
    """获取数据库中有未开赛比赛的联赛列表"""
    try:
        if not prediction_db:
            return jsonify({'success': False}), 500
        return jsonify(upcoming_service.get_available_leagues())
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

