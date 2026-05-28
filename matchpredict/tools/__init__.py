# -*- coding: utf-8 -*-
"""
matchpredict.tools
──────────────────
一次性 / 手动维护用 CLI 工具，不参与 Flask 运行期。

- `sync_historical`      ：拉取历史比赛数据用于 ML 训练
- `import_historical_odds`：从 football-data.co.uk CSV 导入历史赔率
- `eval_snapshot`        ：评估模型预测的离线快照

调用方式：`python -m matchpredict.tools.<name> [args]`
"""
