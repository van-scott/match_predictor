# -*- coding: utf-8 -*-
"""Prediction persistence."""
from typing import Any

from matchpredict.db import prediction_db


class PredictionRepository:
    def __init__(self, db=None):
        self._db = db or prediction_db

    def get_stats(self) -> dict[str, Any]:
        return self._db.get_prediction_stats()

    def can_user_predict(self, user_id: int, user_type: str, daily_used: int) -> bool:
        return self._db.can_user_predict(user_id, user_type, daily_used)

    def save_ai(
        self, match_data, prediction_result, confidence, ai_analysis, user_ip, user_id, username
    ) -> bool:
        return self._db.save_ai_prediction(
            match_data=match_data,
            prediction_result=prediction_result,
            confidence=confidence,
            ai_analysis=ai_analysis,
            user_ip=user_ip,
            user_id=user_id,
            username=username,
        )

    def save_classic(
        self, match_data, prediction_result, confidence, user_ip, user_id, username
    ) -> bool:
        return self._db.save_classic_prediction(
            match_data=match_data,
            prediction_result=prediction_result,
            confidence=confidence,
            user_ip=user_ip,
            user_id=user_id,
            username=username,
        )

    def save_lottery(
        self, match_data, prediction_result, confidence, ai_analysis, user_ip, user_id, username
    ) -> bool:
        return self._db.save_lottery_prediction(
            match_data=match_data,
            prediction_result=prediction_result,
            confidence=confidence,
            ai_analysis=ai_analysis,
            user_ip=user_ip,
            user_id=user_id,
            username=username,
        )

    def increment_predictions(self, user_id: int) -> bool:
        return self._db.increment_user_predictions(user_id)

    def get_user_by_username(self, username: str) -> dict | None:
        return self._db.get_user_by_username(username)

    def get_user_credits(self, user_id: int) -> int:
        return self._db.get_user_credits(user_id)

    def deduct_credits(self, user_id: int, amount: int) -> bool:
        return self._db.deduct_credits(user_id, amount)
