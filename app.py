#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
match_predictor 入口 — 仅负责创建 Flask 应用并启动服务。

架构：
  app.py                       控制层入口
  matchpredict/controllers/    HTTP 路由（薄控制器）
  matchpredict/services/       业务逻辑
  matchpredict/repositories/   数据访问 SQL 封装
  matchpredict/db/             PostgreSQL 连接池 + 表操作
  matchpredict/ml/             ML 特征工程 + 训练 + 推理
  matchpredict/integrations/   外部 API 客户端（AI、彩票爬虫等）
  matchpredict/pipeline/       数据同步流水线（赛程→赔率→ML→结果）
  matchpredict/tools/          一次性 CLI 工具（python -m matchpredict.tools.X）
"""
import os

from matchpredict import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.logger.info('🚀 match_predictor 启动 → http://0.0.0.0:%s', port)
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False)
