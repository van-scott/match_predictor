#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评估快照脚本 (E1/E2)
每日运行，计算并写入 eval_snapshots 表:
  - 整体 accuracy / precision / recall / f1 (H/D/A)
  - Brier score
  - 各联赛准确率
  - Reliability diagram 分桶数据（置信度校准验证）

用法:
  python scripts/eval_snapshot.py          # 全量评估
  python scripts/eval_snapshot.py --days 30  # 只看最近30天
  python scripts/eval_snapshot.py --show     # 仅打印，不写库
"""
import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("numpy 未安装，Brier score 将跳过")


def _ensure_eval_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS eval_snapshots (
            id            SERIAL PRIMARY KEY,
            snapshot_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            period_days   INTEGER,
            total         INTEGER,
            accuracy      NUMERIC(6,4),
            brier_h       NUMERIC(6,4),
            brier_d       NUMERIC(6,4),
            brier_a       NUMERIC(6,4),
            precision_h   NUMERIC(6,4),
            recall_h      NUMERIC(6,4),
            f1_h          NUMERIC(6,4),
            precision_d   NUMERIC(6,4),
            recall_d      NUMERIC(6,4),
            f1_d          NUMERIC(6,4),
            precision_a   NUMERIC(6,4),
            recall_a      NUMERIC(6,4),
            f1_a          NUMERIC(6,4),
            league_stats  JSONB,
            reliability   JSONB
        )
    """)
    conn.commit()


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
    """
    E2: 将预测按概率分桶，计算每桶实际命中率（可视化校准质量）。
    返回: [{bin_mid, pred_prob_mean, actual_hit_rate, count}, ...]
    """
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


def compute_snapshot(db, days: int = None):
    """从 upcoming_fixtures 计算评估快照，返回 snapshot dict。"""
    with db.get_db_connection() as conn:
        cur = conn.cursor()
        date_filter = ""
        params = []
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            date_filter = "AND match_time >= %s"
            params.append(cutoff)

        cur.execute(f"""
            SELECT
                actual_result,
                ml_predicted_result,
                ml_home_prob,
                ml_draw_prob,
                ml_away_prob,
                league_name
            FROM upcoming_fixtures
            WHERE actual_result IS NOT NULL
              AND ml_predicted_result IS NOT NULL
              AND result_correct IS NOT NULL
              {date_filter}
            ORDER BY match_time DESC
        """, params)
        rows = cur.fetchall()

    if not rows:
        logger.warning("没有可评估的比赛记录")
        return None

    y_true  = [r[0] for r in rows]
    y_pred  = [r[1] for r in rows]
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

    # Brier score per class
    def brier(probs, cls):
        if not HAS_NUMPY:
            return None
        vals = [(p, 1 if t == cls else 0) for p, t in zip(probs, y_true) if p is not None]
        if not vals:
            return None
        ps, ys = zip(*vals)
        return round(float(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)), 4)

    brier_h = brier(probs_h, 'H')
    brier_d = brier(probs_d, 'D')
    brier_a = brier(probs_a, 'A')

    # 各联赛统计
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

    # E2: Reliability diagram — 为最大预测概率（即ML推荐那一路）计算校准
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


def print_snapshot(snap):
    print("\n" + "=" * 64)
    print(f"📊 评估快照  |  共 {snap['total']} 场  |  "
          f"{'最近' + str(snap['period_days']) + '天' if snap['period_days'] else '全量'}")
    print("=" * 64)
    print(f"  整体命中率:   {snap['accuracy']*100:.1f}%")
    if snap['brier_h']:
        print(f"  Brier score:  H={snap['brier_h']:.4f}  D={snap['brier_d']:.4f}  A={snap['brier_a']:.4f}")
    print(f"\n  {'类别':<6} {'Precision':>10} {'Recall':>8} {'F1':>8}")
    print("  " + "-" * 34)
    for cls, pk, rk, fk in [('主胜(H)', 'precision_h', 'recall_h', 'f1_h'),
                             ('平局(D)', 'precision_d', 'recall_d', 'f1_d'),
                             ('客胜(A)', 'precision_a', 'recall_a', 'f1_a')]:
        print(f"  {cls:<6} {snap[pk]:>10.4f} {snap[rk]:>8.4f} {snap[fk]:>8.4f}")
    print(f"\n  {'联赛':<10} {'场次':>5} {'命中':>5} {'准确率':>8}")
    print("  " + "-" * 32)
    for lg, d in sorted(snap['league_stats'].items(), key=lambda x: -x[1]['total']):
        if d['total'] >= 5:
            print(f"  {lg:<10} {d['total']:>5} {d['correct']:>5} {d['accuracy']*100:>7.1f}%")
    print("\n  📐 Reliability (H 路):")
    for b in (snap['reliability'] or {}).get('H', []):
        bar = '█' * int(b['actual_rate'] * 20)
        gap = '  ✅' if abs(b['actual_rate'] - b['pred_prob']) < 0.05 else ''
        print(f"    [{b['bin_mid']:.2f}] pred={b['pred_prob']:.3f} actual={b['actual_rate']:.3f} n={b['count']:3d}  {bar}{gap}")
    print("=" * 64)


def save_snapshot(db, snap):
    with db.get_db_connection() as conn:
        _ensure_eval_table(conn)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO eval_snapshots (
                period_days, total, accuracy,
                brier_h, brier_d, brier_a,
                precision_h, recall_h, f1_h,
                precision_d, recall_d, f1_d,
                precision_a, recall_a, f1_a,
                league_stats, reliability
            ) VALUES (%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s)
        """, (
            snap['period_days'], snap['total'], snap['accuracy'],
            snap['brier_h'], snap['brier_d'], snap['brier_a'],
            snap['precision_h'], snap['recall_h'], snap['f1_h'],
            snap['precision_d'], snap['recall_d'], snap['f1_d'],
            snap['precision_a'], snap['recall_a'], snap['f1_a'],
            json.dumps(snap['league_stats'], ensure_ascii=False),
            json.dumps(snap['reliability'], ensure_ascii=False),
        ))
        conn.commit()
    logger.info("✅ 评估快照已写入 eval_snapshots 表")


def main():
    parser = argparse.ArgumentParser(description="生成预测评估快照 (E1/E2)")
    parser.add_argument('--days', type=int, default=None, help='只评估最近 N 天（默认全量）')
    parser.add_argument('--show', action='store_true', help='只打印，不写入数据库')
    args = parser.parse_args()

    from scripts.database import prediction_db
    if not prediction_db:
        logger.error("数据库未连接，无法评估")
        sys.exit(1)

    snap = compute_snapshot(prediction_db, days=args.days)
    if not snap:
        sys.exit(0)

    print_snapshot(snap)

    if not args.show:
        save_snapshot(prediction_db, snap)


if __name__ == "__main__":
    main()
