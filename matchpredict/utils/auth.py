# -*- coding: utf-8 -*-
"""认证工具函数。"""
import hashlib

from flask import session

from matchpredict.data import prediction_db


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_current_user():
    if 'user_id' in session and prediction_db:
        return prediction_db.get_user_by_username(session['username'])
    return None


def require_login() -> bool:
    return get_current_user() is None
