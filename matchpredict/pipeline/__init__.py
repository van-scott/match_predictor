# -*- coding: utf-8 -*-
"""
matchpredict.pipeline
─────────────────────
数据同步与预测流水线包。

快速用法：
    from matchpredict.pipeline import run_pipeline
    run_pipeline(mode="full")
"""
from matchpredict.pipeline.runner import run_pipeline

__all__ = ["run_pipeline"]
