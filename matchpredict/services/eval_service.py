# -*- coding: utf-8 -*-
"""ML 模型评估快照：计算、持久化、API 输出。"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from matchpredict.data import prediction_db
from matchpredict.repositories.eval_repository import EvalRepository

logger = logging.getLogger(__name__)

DEFAULT_PERIOD_DAYS = 30
STALE_HOURS = 24


def _safe_div(a, b, default=0.0):
    return a / b if b > 0 else default


def _prf(y_true, y_pred, cls):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p == cls)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != cls and p == cls)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p != cls)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return round(precision, 4), round(recall, 4), round(f1, 4)


def build_reliability_diagram(probs, actuals, n_bins=10):
    bins = []
    bin_size = 1.0 / n_bins
    for i in range(n_bins):
        lo = i * bin_size
        hi = (i + 1) * bin_size
        bucket = [(p, a) for p, a in zip(probs, actuals) if lo <= p < hi]
        if not bucket:
            continue
        mean_pred = sum(p for p, _ in bucket) / len(bucket)
        actual_hit = sum(1 for _, a in bucket if a) / len(bucket)
        bins.append({
            'bin_mid': round((lo + hi) / 2, 3),
            'pred_prob': round(mean_pred, 4),
            'actual_rate': round(actual_hit, 4),
            'count': len(bucket),
        })
    return bins


def compute_snapshot_from_rows(rows: list[tuple], days: Optional[int] = None) -> Optional[dict]:
    if not rows:
        return None

    y_true = [r[0] for r in rows]
    y_pred = [r[1] for r in rows]
    probs_h = [float(r[2]) if r[2] else None for r in rows]
    probs_d = [float(r[3]) if r[3] else None for r in rows]
    probs_a = [float(r[4]) if r[4] else None for r in rows]
    leagues = [r[5] for r in rows]

    n = len(y_true)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    accuracy = round(_safe_div(correct, n), 4)

    prec_h, rec_h, f1_h = _prf(y_true, y_pred, 'H')
    prec_d, rec_d, f1_d = _prf(y_true, y_pred, 'D')
    prec_a, rec_a, f1_a = _prf(y_true, y_pred, 'A')

    def brier(probs, cls):
        vals = [(p, 1 if t == cls else 0) for p, t in zip(probs, y_true) if p is not None]
        if not vals:
            return None
        ps, ys = zip(*vals)
        return round(float(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)), 4)

    brier_h = brier(probs_h, 'H')
    brier_d = brier(probs_d, 'D')
    brier_a = brier(probs_a, 'A')

    league_stats = {}
    for lg, t, p in zip(leagues, y_true, y_pred):
        if lg not in league_stats:
            league_stats[lg] = {'total': 0, 'correct': 0}
        league_stats[lg]['total'] += 1
        if t == p:
            league_stats[lg]['correct'] += 1
    for lg in league_stats:
        d = league_stats[lg]
        d['accuracy'] = round(_safe_div(d['correct'], d['total']), 4)

    reliability_data = {}
    for cls, probs in [('H', probs_h), ('D', probs_d), ('A', probs_a)]:
        actuals_bin = [1 if t == cls else 0 for t in y_true]
        valid = [(p, a) for p, a in zip(probs, actuals_bin) if p is not None]
        if valid:
            ps, acs = zip(*valid)
            reliability_data[cls] = build_reliability_diagram(list(ps), list(acs))

    return {
        'period_days': days,
        'total': n,
        'accuracy': accuracy,
        'brier_h': brier_h,
        'brier_d': brier_d,
        'brier_a': brier_a,
        'precision_h': prec_h, 'recall_h': rec_h, 'f1_h': f1_h,
        'precision_d': prec_d, 'recall_d': rec_d, 'f1_d': f1_d,
        'precision_a': prec_a, 'recall_a': rec_a, 'f1_a': f1_a,
        'league_stats': league_stats,
        'reliability': reliability_data,
    }


class EvalService:
    def __init__(self, db=None):
        self._db = db or prediction_db
        self._repo = EvalRepository(self._db) if self._db else None

    def compute_snapshot(self, days: Optional[int] = DEFAULT_PERIOD_DAYS) -> Optional[dict]:
        if not self._repo:
            return None
        rows = self._repo.fetch_eval_rows(days=days)
        return compute_snapshot_from_rows(rows, days=days)

    def run_and_save(self, days: Optional[int] = DEFAULT_PERIOD_DAYS) -> Optional[dict]:
        if not self._repo:
            logger.warning('评估快照：数据库不可用')
            return None
        snap = self.compute_snapshot(days=days)
        if not snap:
            logger.info('评估快照：无可评估比赛，跳过写入')
            return None
        snap_id = self._repo.save_snapshot(snap)
        logger.info('✅ 评估快照已写入 eval_snapshots #%s (%s 场, acc=%.1f%%)',
                    snap_id, snap['total'], snap['accuracy'] * 100)
        saved = self._repo.fetch_latest(period_days=days)
        return saved

    def _is_stale(self, snapshot: Optional[dict]) -> bool:
        if not snapshot or not snapshot.get('snapshot_at'):
            return True
        try:
            snap_at = datetime.fromisoformat(snapshot['snapshot_at'])
            if snap_at.tzinfo is None:
                snap_at = snap_at.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) - snap_at > timedelta(hours=STALE_HOURS)
        except Exception:
            return True

    def _format_for_api(self, snap: dict) -> dict:
        return {
            'snapshot_at': snap.get('snapshot_at'),
            'period_days': snap.get('period_days'),
            'total': snap.get('total'),
            'accuracy': round(float(snap['accuracy']) * 100, 1) if snap.get('accuracy') is not None else None,
            'brier': {
                'H': snap.get('brier_h'),
                'D': snap.get('brier_d'),
                'A': snap.get('brier_a'),
            },
            'metrics': {
                'H': {'precision': snap.get('precision_h'), 'recall': snap.get('recall_h'), 'f1': snap.get('f1_h')},
                'D': {'precision': snap.get('precision_d'), 'recall': snap.get('recall_d'), 'f1': snap.get('f1_d')},
                'A': {'precision': snap.get('precision_a'), 'recall': snap.get('recall_a'), 'f1': snap.get('f1_a')},
            },
            'reliability': snap.get('reliability') or {},
            'league_stats': [
                {'league': lg, **d}
                for lg, d in sorted(
                    (snap.get('league_stats') or {}).items(),
                    key=lambda x: -x[1].get('total', 0),
                )
                if d.get('total', 0) >= 3
            ],
        }

    def get_dashboard(self, days: int = DEFAULT_PERIOD_DAYS, refresh: bool = False) -> dict[str, Any]:
        if not self._repo:
            return {'success': False, 'message': '数据库不可用'}

        latest = self._repo.fetch_latest(period_days=days)
        if refresh or self._is_stale(latest):
            latest = self.run_and_save(days=days) or latest

        history_raw = self._repo.fetch_history(limit=15, period_days=days)
        history = [
            {
                'snapshot_at': h.get('snapshot_at'),
                'accuracy': round(float(h['accuracy']) * 100, 1) if h.get('accuracy') is not None else None,
                'total': h.get('total'),
            }
            for h in reversed(history_raw)
        ]

        if not latest:
            return {
                'success': True,
                'latest': None,
                'history': [],
                'message': '暂无评估数据，比赛同步后会自动生成',
            }

        return {
            'success': True,
            'latest': self._format_for_api(latest),
            'history': history,
        }


eval_service = EvalService()
