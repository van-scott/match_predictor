# -*- coding: utf-8 -*-
"""
CLI 引导工具
─────────────
所有 `python -m matchpredict.tools.X` 入口共用的初始化：
  1. 确保项目根在 sys.path（兼容直接 `python matchpredict/tools/X.py` 调用）
  2. 加载 .env 文件到 os.environ（若存在）
  3. 配置默认日志格式

调用方式：
    from matchpredict.utils.bootstrap import init_cli
    init_cli()
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    """matchpredict 包的父目录 == 项目根。"""
    return Path(__file__).resolve().parents[2]


def _load_env(env_path: Path) -> int:
    """简单 .env 解析器（避免引入 python-dotenv 依赖）。"""
    if not env_path.exists():
        return 0
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val
            loaded += 1
    return loaded


def init_cli(log_level: str = "INFO") -> None:
    """供 tools/ 下的 CLI 入口使用：sys.path + .env + logging。"""
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _load_env(root / ".env")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
