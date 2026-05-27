# -*- coding: utf-8 -*-
"""业务逻辑层。"""

from matchpredict.services.ai_prediction_service import ai_prediction_service
from matchpredict.services.smart_predict_service import smart_predict_service
from matchpredict.services.upcoming_service import upcoming_service

__all__ = [
    'ai_prediction_service',
    'smart_predict_service',
    'upcoming_service',
]
