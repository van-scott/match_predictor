# -*- coding: utf-8 -*-
"""
数据层入口：数据库访问与同步脚本。

- `prediction_db`：PostgreSQL 连接池与表操作（实现于 scripts/database.py）
- `scripts/sync_*.py`：定时同步 CLI，由 scheduler 子进程调用
"""

from scripts.database import prediction_db

__all__ = ['prediction_db']
