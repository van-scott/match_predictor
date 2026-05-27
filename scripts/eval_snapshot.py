#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评估快照 CLI — 调用 matchpredict.services.eval_service

用法:
  python scripts/eval_snapshot.py              # 最近30天，写入库
  python scripts/eval_snapshot.py --days 30    # 指定窗口
  python scripts/eval_snapshot.py --show         # 只打印，不写库
  python scripts/eval_snapshot.py --all          # 全量历史
"""
import os
import sys
import logging
import argparse

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


def print_snapshot(snap):
    acc = snap.get('accuracy')
    acc_pct = acc * 100 if acc is not None and acc <= 1 else acc
    print("\n" + "=" * 64)
    period = snap.get('period_days')
    print(f"📊 评估快照  |  共 {snap['total']} 场  |  "
          f"{'最近' + str(period) + '天' if period else '全量'}")
    print("=" * 64)
    print(f"  整体命中率:   {acc_pct:.1f}%")
    if snap.get('brier_h') is not None:
        print(f"  Brier score:  H={snap['brier_h']:.4f}  D={snap['brier_d']:.4f}  A={snap['brier_a']:.4f}")
    print(f"\n  {'类别':<6} {'Precision':>10} {'Recall':>8} {'F1':>8}")
    print("  " + "-" * 34)
    for cls, pk, rk, fk in [('主胜(H)', 'precision_h', 'recall_h', 'f1_h'),
                             ('平局(D)', 'precision_d', 'recall_d', 'f1_d'),
                             ('客胜(A)', 'precision_a', 'recall_a', 'f1_a')]:
        print(f"  {cls:<6} {snap[pk]:>10.4f} {snap[rk]:>8.4f} {snap[fk]:>8.4f}")
    print(f"\n  {'联赛':<10} {'场次':>5} {'命中':>5} {'准确率':>8}")
    print("  " + "-" * 32)
    for lg, d in sorted((snap.get('league_stats') or {}).items(), key=lambda x: -x[1]['total']):
        if d['total'] >= 5:
            print(f"  {lg:<10} {d['total']:>5} {d['correct']:>5} {d['accuracy']*100:>7.1f}%")
    print("\n  📐 Reliability (H 路):")
    for b in (snap.get('reliability') or {}).get('H', []):
        bar = '█' * int(b['actual_rate'] * 20)
        gap = '  ✅' if abs(b['actual_rate'] - b['pred_prob']) < 0.05 else ''
        print(f"    [{b['bin_mid']:.2f}] pred={b['pred_prob']:.3f} actual={b['actual_rate']:.3f} n={b['count']:3d}  {bar}{gap}")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="生成预测评估快照")
    parser.add_argument('--days', type=int, default=30, help='评估最近 N 天（默认30）')
    parser.add_argument('--all', action='store_true', help='全量历史（days=None）')
    parser.add_argument('--show', action='store_true', help='只打印，不写入数据库')
    args = parser.parse_args()

    from matchpredict.services.eval_service import eval_service
    if not eval_service._repo:
        logger.error("数据库未连接，无法评估")
        sys.exit(1)

    days = None if args.all else args.days
    snap = eval_service.compute_snapshot(days=days)
    if not snap:
        logger.warning("没有可评估的比赛记录")
        sys.exit(0)

    print_snapshot(snap)

    if not args.show:
        eval_service.run_and_save(days=days)


if __name__ == "__main__":
    main()
