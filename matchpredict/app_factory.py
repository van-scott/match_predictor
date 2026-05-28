# -*- coding: utf-8 -*-
"""Flask 应用工厂。"""
import logging
import os

from flask import Flask

from matchpredict.controllers import register_blueprints, register_hooks
from matchpredict.db import prediction_db
from matchpredict.extensions import configure_app, initialize_services
from matchpredict.services.scheduler_service import setup_scheduler

logging.basicConfig(level=logging.INFO)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'),
    )

    configure_app(app)
    register_hooks(app)
    register_blueprints(app)

    with app.app_context():
        initialize_services(app)

    if prediction_db:
        try:
            prediction_db.ensure_credits_columns()
            prediction_db.ensure_ai_config_columns()
        except Exception as e:
            app.logger.warning('字段初始化跳过: %s', e)

    setup_scheduler(app)
    return app
