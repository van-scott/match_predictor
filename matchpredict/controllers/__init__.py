# -*- coding: utf-8 -*-
"""注册所有 HTTP 蓝图。"""
from flask import Flask, request

from matchpredict.controllers import (
    pages,
    auth,
    matches,
    predictions,
    ai,
    accuracy,
    admin,
    world_cup,
    health,
)


def register_blueprints(app: Flask) -> None:
    for mod in (pages, auth, matches, predictions, ai, accuracy, admin, world_cup, health):
        app.register_blueprint(mod.bp)


def register_hooks(app: Flask) -> None:
    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get('Origin')
        if origin:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        if request.method == 'OPTIONS':
            response.status_code = 204
        return response
