# -*- coding: utf-8 -*-
"""世界杯预测业务逻辑。"""
import math
import random

from matchpredict.domain.world_cup import WC_TEAM_WEIGHTS


def predict_match(home: str, away: str, ho: float = None, do_: float = None, ao: float = None) -> dict:
    w_h = WC_TEAM_WEIGHTS.get(home, 75)
    w_a = WC_TEAM_WEIGHTS.get(away, 75)
    w_h_adj = w_h * 1.05

    exp_h = math.exp(w_h_adj / 20)
    exp_d = math.exp((w_h_adj + w_a) / 45)
    exp_a = math.exp(w_a / 20)
    total = exp_h + exp_d + exp_a
    prob_h, prob_d, prob_a = exp_h / total, exp_d / total, exp_a / total

    if ho and do_ and ao:
        implied_h, implied_d, implied_a = 1 / ho, 1 / do_, 1 / ao
        s = implied_h + implied_d + implied_a
        implied_h, implied_d, implied_a = implied_h / s, implied_d / s, implied_a / s
        prob_h = prob_h * 0.7 + implied_h * 0.3
        prob_d = prob_d * 0.7 + implied_d * 0.3
        prob_a = prob_a * 0.7 + implied_a * 0.3

    lambda_h = max(0.5, (w_h_adj / w_a) * 1.3)
    lambda_a = max(0.3, (w_a / w_h_adj) * 1.1)
    score_h = min(5, round(lambda_h * random.uniform(0.7, 1.3)))
    score_a = min(5, round(lambda_a * random.uniform(0.7, 1.3)))

    if prob_h >= prob_d and prob_h >= prob_a:
        rec = '主胜'
    elif prob_d >= prob_a:
        rec = '平局'
    else:
        rec = '客胜'

    return {
        'home_team': home,
        'away_team': away,
        'probabilities': {
            'home': round(prob_h, 3),
            'draw': round(prob_d, 3),
            'away': round(prob_a, 3),
        },
        'home_score_pred': score_h,
        'away_score_pred': score_a,
        'recommendation': rec,
    }
