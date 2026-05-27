# -*- coding: utf-8 -*-
"""HTTP 控制器 — 仅处理请求/响应，业务逻辑在 services 层。"""
from flask import Blueprint, request, jsonify, render_template, session, current_app, make_response
import os
import json
import logging
import requests
import hashlib
import psycopg2
import math
import subprocess
import threading
import uuid
import time as _time
from datetime import datetime, timedelta, date

from matchpredict.extensions import prediction_db, lottery_spider, ai_predictor, get_ai_predictor_class
from matchpredict.utils.auth import hash_password, get_current_user, require_login
from matchpredict.domain.team_names import TEAM_NAME_CN
from matchpredict.domain.leagues import LEAGUES, TEAMS_DATA
from matchpredict.domain.world_cup import WC_TEAM_WEIGHTS
from matchpredict.services.world_cup_service import predict_match as wc_predict_match

bp = Blueprint('world_cup', __name__)

@bp.route('/api/wc/predict', methods=['POST'])
def wc_predict():
    """世界杯单场预测（消耗1积分）"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        # 扣除积分
        if prediction_db:
            ok = prediction_db.deduct_credits(current_user['id'], 1)
            if not ok:
                credits = prediction_db.get_user_credits(current_user['id'])
                return jsonify({
                    'success': False,
                    'error_code': 'INSUFFICIENT_CREDITS',
                    'message': '积分不足',
                    'credits': credits,
                    'cost': 1,
                }), 402

        data = request.get_json() or {}
        home = data.get('home_team', '')
        away = data.get('away_team', '')
        ho = float(data['ho']) if data.get('ho') else None
        do_ = float(data['do']) if data.get('do') else None
        ao = float(data['ao']) if data.get('ao') else None

        if not home or not away:
            return jsonify({'success': False, 'message': '请选择主队和客队'}), 400

        prediction = wc_predict_match(home, away, ho, do_, ao)
        credits = prediction_db.get_user_credits(current_user['id']) if prediction_db else 0

        return jsonify({'success': True, 'prediction': prediction, 'credits': credits})

    except Exception as e:
        current_app.logger.error(f"世界杯预测失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/wc/simulate', methods=['POST'])
def wc_simulate():
    """淘汰赛全赛程模拟（消耗5积分）"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        if prediction_db:
            ok = prediction_db.deduct_credits(current_user['id'], 5)
            if not ok:
                credits = prediction_db.get_user_credits(current_user['id'])
                return jsonify({
                    'success': False,
                    'error_code': 'INSUFFICIENT_CREDITS',
                    'message': '积分不足（本次需5分）',
                    'credits': credits,
                    'cost': 5,
                }), 402

        import random

        teams = list(WC_TEAM_WEIGHTS.keys())  # 16支
        random.shuffle(teams)

        def sim_match(home, away):
            p = wc_predict_match(home, away)
            r = random.random()
            if r < p['probabilities']['home']:
                winner = home
            elif r < p['probabilities']['home'] + p['probabilities']['draw']:
                # 淘汰赛无平局，胜率高者晋级
                winner = home if p['probabilities']['home'] >= p['probabilities']['away'] else away
            else:
                winner = away
            return {
                'home': home, 'away': away,
                'score': f"{p['home_score_pred']}-{p['away_score_pred']}",
                'winner': winner,
            }

        # 1/8 决赛
        r16_matches = [sim_match(teams[i*2], teams[i*2+1]) for i in range(8)]
        r16_winners = [m['winner'] for m in r16_matches]

        # 1/4 决赛
        qf_matches = [sim_match(r16_winners[i*2], r16_winners[i*2+1]) for i in range(4)]
        qf_winners = [m['winner'] for m in qf_matches]

        # 半决赛
        sf_matches = [sim_match(qf_winners[0], qf_winners[1]), sim_match(qf_winners[2], qf_winners[3])]
        sf_winners = [m['winner'] for m in sf_matches]

        # 决赛
        final_match = sim_match(sf_winners[0], sf_winners[1])

        bracket = {
            'r16': r16_matches,
            'qf': qf_matches,
            'sf': sf_matches,
            'final': [final_match],
        }

        credits = prediction_db.get_user_credits(current_user['id']) if prediction_db else 0
        return jsonify({'success': True, 'bracket': bracket, 'credits': credits})

    except Exception as e:
        current_app.logger.error(f"世界杯模拟失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 新智能预测 API：未开赛比赛 + ML 概率 + AI 分析 + 赔率变动
# ═══════════════════════════════════════════════════════════════════════════

