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
from matchpredict.utils.goals import calc_predicted_goals, interpret_odds_signal

try:
    from scripts.ai_predictor import AIFootballPredictor
except ImportError:
    AIFootballPredictor = None

bp = Blueprint("health", __name__)

@bp.route('/health')
def health():
    """健康检查"""
    return "OK", 200

@bp.route('/data/<filename>')
def serve_data_files(filename):
    """提供数据文件访问"""
    try:
        from flask import send_from_directory
        return send_from_directory('data', filename)
    except Exception as e:
        current_app.logger.error(f"提供数据文件失败: {e}")
        return jsonify({'error': '文件未找到'}), 404

# 用户认证路由
