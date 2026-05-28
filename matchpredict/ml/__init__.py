# -*- coding: utf-8 -*-
"""
matchpredict.ml
───────────────
机器学习训练与推理。

- `features` ：从数据库构建训练特征矩阵
- `training`：模型训练管道（CLI: `python -m matchpredict.ml.training`），
              同时导出 `predict_probabilities` 供在线推理使用
"""
from matchpredict.ml.features import (
    load_historical_matches,
    compute_team_stats,
    build_match_feature_matrix,
    compute_h2h,
)
from matchpredict.ml.training import predict_probabilities

__all__ = [
    "load_historical_matches",
    "compute_team_stats",
    "build_match_feature_matrix",
    "compute_h2h",
    "predict_probabilities",
]
