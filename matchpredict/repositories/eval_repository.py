# -*- coding: utf-8 -*-
"""ML 评估快照数据库访问。"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


class EvalRepository:
    def __init__(self, db):
        self._db = db

    def ensure_table(self) -> None:
        with self._db.get_db_connection() as conn:
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

    def fetch_eval_rows(self, days: Optional[int] = None) -> list[tuple]:
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            date_filter = ''
            params: list[Any] = []
            if days:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                date_filter = 'AND match_time >= %s'
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
            return cur.fetchall()

    def save_snapshot(self, snap: dict) -> int:
        self.ensure_table()
        with self._db.get_db_connection() as conn:
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
                RETURNING id
            """, (
                snap.get('period_days'), snap['total'], snap['accuracy'],
                snap.get('brier_h'), snap.get('brier_d'), snap.get('brier_a'),
                snap['precision_h'], snap['recall_h'], snap['f1_h'],
                snap['precision_d'], snap['recall_d'], snap['f1_d'],
                snap['precision_a'], snap['recall_a'], snap['f1_a'],
                json.dumps(snap.get('league_stats') or {}, ensure_ascii=False),
                json.dumps(snap.get('reliability') or {}, ensure_ascii=False),
            ))
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id

    def fetch_latest(self, period_days: Optional[int] = None) -> Optional[dict]:
        self.ensure_table()
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            if period_days is not None:
                cur.execute("""
                    SELECT id, snapshot_at, period_days, total, accuracy,
                           brier_h, brier_d, brier_a,
                           precision_h, recall_h, f1_h,
                           precision_d, recall_d, f1_d,
                           precision_a, recall_a, f1_a,
                           league_stats, reliability
                    FROM eval_snapshots
                    WHERE period_days IS NOT DISTINCT FROM %s
                    ORDER BY snapshot_at DESC
                    LIMIT 1
                """, (period_days,))
            else:
                cur.execute("""
                    SELECT id, snapshot_at, period_days, total, accuracy,
                           brier_h, brier_d, brier_a,
                           precision_h, recall_h, f1_h,
                           precision_d, recall_d, f1_d,
                           precision_a, recall_a, f1_a,
                           league_stats, reliability
                    FROM eval_snapshots
                    ORDER BY snapshot_at DESC
                    LIMIT 1
                """)
            row = cur.fetchone()
            return self._row_to_dict(row) if row else None

    def fetch_history(self, limit: int = 20, period_days: Optional[int] = 30) -> list[dict]:
        self.ensure_table()
        with self._db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, snapshot_at, period_days, total, accuracy,
                       brier_h, brier_d, brier_a,
                       precision_h, recall_h, f1_h,
                       precision_d, recall_d, f1_d,
                       precision_a, recall_a, f1_a,
                       league_stats, reliability
                FROM eval_snapshots
                WHERE period_days IS NOT DISTINCT FROM %s
                ORDER BY snapshot_at DESC
                LIMIT %s
            """, (period_days, limit))
            return [self._row_to_dict(r) for r in cur.fetchall()]

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        (rid, snap_at, period_days, total, accuracy,
         brier_h, brier_d, brier_a,
         ph, rh, fh, pd, rd, fd, pa, ra, fa,
         league_stats, reliability) = row
        return {
            'id': rid,
            'snapshot_at': snap_at.isoformat() if snap_at else None,
            'period_days': period_days,
            'total': total,
            'accuracy': float(accuracy) if accuracy is not None else None,
            'brier_h': float(brier_h) if brier_h is not None else None,
            'brier_d': float(brier_d) if brier_d is not None else None,
            'brier_a': float(brier_a) if brier_a is not None else None,
            'precision_h': float(ph) if ph is not None else None,
            'recall_h': float(rh) if rh is not None else None,
            'f1_h': float(fh) if fh is not None else None,
            'precision_d': float(pd) if pd is not None else None,
            'recall_d': float(rd) if rd is not None else None,
            'f1_d': float(fd) if fd is not None else None,
            'precision_a': float(pa) if pa is not None else None,
            'recall_a': float(ra) if ra is not None else None,
            'f1_a': float(fa) if fa is not None else None,
            'league_stats': league_stats if isinstance(league_stats, dict) else json.loads(league_stats or '{}'),
            'reliability': reliability if isinstance(reliability, dict) else json.loads(reliability or '{}'),
        }
