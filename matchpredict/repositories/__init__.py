# -*- coding: utf-8 -*-
"""数据访问层。"""
from matchpredict.repositories.accuracy_repository import AccuracyRepository
from matchpredict.repositories.fixture_repository import FixtureRepository
from matchpredict.repositories.upcoming_repository import UpcomingRepository
from matchpredict.repositories.prediction_repository import PredictionRepository

__all__ = [
    'AccuracyRepository',
    'FixtureRepository',
    'UpcomingRepository',
    'PredictionRepository',
]
