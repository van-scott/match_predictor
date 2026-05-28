# -*- coding: utf-8 -*-
"""应用扩展与全局服务实例（在 create_app 时初始化）。"""
import os
import logging
from datetime import timedelta

from flask import Flask

from matchpredict.db import prediction_db

lottery_spider = None
ai_predictor = None

logger = logging.getLogger(__name__)


def get_ai_predictor_class():
    try:
        from matchpredict.integrations.ai_predictor import AIFootballPredictor
        return AIFootballPredictor
    except ImportError:
        return None


def initialize_services(app: Flask) -> None:
    """初始化彩票爬虫、默认 AI 预测器等全局服务。"""
    global lottery_spider, ai_predictor

    try:
        from matchpredict.integrations.lottery_api import ChinaSportsLotterySpider
    except ImportError:
        ChinaSportsLotterySpider = None

    try:
        if ChinaSportsLotterySpider:
            lottery_spider = ChinaSportsLotterySpider()
            app.logger.info('彩票 API 初始化成功')
        else:
            app.logger.warning('彩票 API 类未加载')
    except Exception as e:
        app.logger.error(f'彩票 API 初始化失败: {e}')
        lottery_spider = None

    AIFootballPredictor = get_ai_predictor_class()
    try:
        if AIFootballPredictor:
            gemini_api_key = os.environ.get('GEMINI_API_KEY')
            gemini_model = os.environ.get(
                'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-06-17'
            )
            if not gemini_api_key:
                app.logger.warning('GEMINI_API_KEY 未设置，AI 预测器不可用')
                ai_predictor = None
            else:
                ai_predictor = AIFootballPredictor(
                    api_key=gemini_api_key,
                    model_name=gemini_model,
                )
                app.logger.info('AI 预测器初始化成功')
        else:
            app.logger.warning('AI 预测器类未加载')
    except Exception as e:
        app.logger.error(f'AI 预测器初始化失败: {e}')
        ai_predictor = None


def configure_app(app: Flask) -> None:
    """Flask 应用配置（Session、密钥等）。"""
    is_local = (
        os.environ.get('FLASK_ENV', 'production') == 'development'
        or os.environ.get('FLASK_DEBUG', '0') == '1'
        or os.environ.get('LOCAL_DEV', '0') == '1'
    )
    app.secret_key = os.environ.get(
        'SECRET_KEY', 'your-secret-key-change-in-production'
    )
    app.config.update(
        SESSION_COOKIE_NAME='mp_session',
        SESSION_COOKIE_SAMESITE='Lax' if is_local else os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
        SESSION_COOKIE_SECURE=False if is_local else True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_DOMAIN=None if is_local else os.environ.get('SESSION_COOKIE_DOMAIN'),
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
