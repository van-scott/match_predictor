# -*- coding: utf-8 -*-
"""
matchpredict.db
───────────────
数据库访问层。

对外仅暴露 `prediction_db` 单例：内部维护 ThreadedConnectionPool，
通过 `with prediction_db.get_db_connection() as conn:` 获取连接。
"""
from matchpredict.db.prediction_db import prediction_db, PredictionDatabase

__all__ = ["prediction_db", "PredictionDatabase"]
