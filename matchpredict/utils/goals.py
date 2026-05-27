# -*- coding: utf-8 -*-
"""比分与赔率推算工具。"""
import math


def calc_predicted_goals(db_home, db_away, ml_pred, h_odds, d_odds, a_odds):
    """
    推算预测比分，优先级：DB 已有 → ML/赔率概率泊松 → None
    """
    if db_home is not None and db_away is not None:
        return int(db_home), int(db_away)

    ph = pa = None
    if ml_pred:
        ph = ml_pred.get('home_prob')
        pa = ml_pred.get('away_prob')
    elif h_odds and d_odds and a_odds:
        rh = 1 / float(h_odds)
        ra = 1 / float(a_odds)
        tot = rh + 1 / float(d_odds) + ra
        ph, pa = rh / tot, ra / tot

    if ph and pa:
        lam_h = max(-math.log(max(1 - float(ph), 0.01)) * 1.5, 0.3)
        lam_a = max(-math.log(max(1 - float(pa), 0.01)) * 1.5, 0.3)
        return min(round(lam_h), 5), min(round(lam_a), 5)
    return None, None


def interpret_odds_signal(open_odds: float, current_odds: float) -> str:
    if open_odds <= 0:
        return 'neutral'
    change_pct = (current_odds - open_odds) / open_odds
    if change_pct <= -0.10:
        return 'strong_down'
    if change_pct <= -0.04:
        return 'down'
    if change_pct >= 0.10:
        return 'strong_up'
    if change_pct >= 0.04:
        return 'up'
    return 'stable'
