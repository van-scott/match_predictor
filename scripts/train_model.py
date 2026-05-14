#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库驱动的 ML 训练管道
1. 从数据库拉取历史比赛
2. 计算特征矩阵
3. 训练 RandomForest + GradientBoosting
4. 评估并保存最佳模型
用法: python scripts/train_model.py [--league 英超] [--output models/]
"""
import os
import sys
import pickle
import logging
import argparse
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import (accuracy_score, classification_report,
                              confusion_matrix, log_loss)
from sklearn.preprocessing import LabelEncoder
from sklearn.calibration import CalibratedClassifierCV

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.feature_engineering import (
    load_historical_matches,
    compute_team_stats,
    build_match_feature_matrix,
    save_team_ratings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 模型存储路径
MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'models')

# 用于训练的特征列（不含 meta 字段和 label）
META_COLS = {'match_id', 'match_date', 'league', 'home_team', 'away_team', 'result'}


# ─────────────────────────────────────────────────────────────────────────────
# 特征提取
# ─────────────────────────────────────────────────────────────────────────────

def get_feature_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c not in META_COLS]


def prepare_xy(feat_df: pd.DataFrame, league: str = None):
    """准备 X（特征矩阵）和 y（标签）"""
    if league:
        feat_df = feat_df[feat_df['league'] == league].copy()
        logger.info(f"过滤联赛 [{league}] 后剩余 {len(feat_df)} 场")

    feat_df = feat_df.dropna(subset=['result'])
    feat_cols = get_feature_cols(feat_df)
    X = feat_df[feat_cols].fillna(0).values
    y = feat_df['result'].values
    return X, y, feat_cols, feat_df


# ─────────────────────────────────────────────────────────────────────────────
# 模型训练
# ─────────────────────────────────────────────────────────────────────────────

def train_models(X, y, feat_cols: list) -> dict:
    """训练两个模型并返回最优模型包"""
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    # ── RandomForest ──────────────────────────────────────────────────────────
    logger.info("🌲 训练 RandomForest ...")
    rf = RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=5,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1,
    )
    rf_scores = cross_val_score(rf, X, y, cv=cv, scoring='accuracy')
    logger.info(f"   RF CV 准确率: {rf_scores.mean():.4f} ± {rf_scores.std():.4f}")
    rf.fit(X, y)

    # ── GradientBoosting ──────────────────────────────────────────────────────
    logger.info("🚀 训练 GradientBoosting ...")
    gb = GradientBoostingClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        subsample=0.8,
        random_state=42,
    )
    gb_scores = cross_val_score(gb, X, y, cv=cv, scoring='accuracy')
    logger.info(f"   GB CV 准确率: {gb_scores.mean():.4f} ± {gb_scores.std():.4f}")
    gb.fit(X, y)

    # ── 选最优 ────────────────────────────────────────────────────────────────
    best_name = 'RandomForest' if rf_scores.mean() >= gb_scores.mean() else 'GradientBoosting'
    best_model = rf if best_name == 'RandomForest' else gb
    best_score = max(rf_scores.mean(), gb_scores.mean())
    logger.info(f"🏆 最优模型: {best_name}  CV Accuracy = {best_score:.4f}")

    # ── 校准概率（Platt scaling）─────────────────────────────────────────────
    calibrated = CalibratedClassifierCV(best_model, method='sigmoid', cv=5)
    calibrated.fit(X, y)

    return {
        'model':        calibrated,
        'base_model':   best_model,
        'model_name':   best_name,
        'cv_accuracy':  best_score,
        'feat_cols':    feat_cols,
        'classes':      best_model.classes_.tolist(),
        'rf':           {'model': rf, 'cv_scores': rf_scores.tolist()},
        'gb':           {'model': gb, 'cv_scores': gb_scores.tolist()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 模型评估
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_model(model_pkg: dict, X, y, feat_df: pd.DataFrame):
    """打印完整评估报告和特征重要性"""
    model = model_pkg['base_model']
    y_pred = model.predict(X)
    acc = accuracy_score(y, y_pred)

    print("\n" + "=" * 60)
    print(f"📊 模型评估报告  ({model_pkg['model_name']})")
    print("=" * 60)
    print(f"训练集准确率: {acc:.4f}  ({acc*100:.1f}%)")
    print(f"CV 5折准确率: {model_pkg['cv_accuracy']:.4f}")
    print()
    print(classification_report(y, y_pred, target_names=['客胜(A)', '平局(D)', '主胜(H)']))
    print("混淆矩阵 [A / D / H]:")
    print(confusion_matrix(y, y_pred, labels=['A', 'D', 'H']))

    # 特征重要性
    if hasattr(model, 'feature_importances_'):
        fi = pd.Series(model.feature_importances_, index=model_pkg['feat_cols'])
        fi_top = fi.nlargest(15)
        print("\n🔑 Top-15 重要特征:")
        for name, imp in fi_top.items():
            bar = '█' * int(imp * 100)
            print(f"  {name:<40} {imp:.4f}  {bar}")

    # 联赛分组准确率
    if 'league' in feat_df.columns:
        print("\n🌍 各联赛准确率:")
        for league, grp in feat_df.groupby('league'):
            feat_cols = model_pkg['feat_cols']
            X_l = grp[feat_cols].fillna(0).values
            y_l = grp['result'].values
            acc_l = accuracy_score(y_l, model.predict(X_l))
            print(f"  {league}: {acc_l:.4f}  ({len(grp)} 场)")


# ─────────────────────────────────────────────────────────────────────────────
# 保存模型
# ─────────────────────────────────────────────────────────────────────────────

def save_model(model_pkg: dict, output_dir: str, suffix: str = 'all'):
    """保存模型包到 pkl 文件"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"match_predictor_{suffix}.pkl")
    with open(path, 'wb') as f:
        pickle.dump(model_pkg, f)
    logger.info(f"💾 模型已保存: {path}")
    return path


def load_model(suffix: str = 'all', model_dir: str = None) -> dict:
    """加载已保存的模型包"""
    if model_dir is None:
        model_dir = MODEL_DIR
    path = os.path.join(model_dir, f"match_predictor_{suffix}.pkl")
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return pickle.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# 推理接口（供 ai_predictor.py 调用）
# ─────────────────────────────────────────────────────────────────────────────

def predict_probabilities(home_team: str, away_team: str,
                           team_stats: dict, df_hist: pd.DataFrame,
                           model_pkg: dict) -> dict:
    """
    根据球队名和统计数据预测胜平负概率。
    返回: {'H': 0.45, 'D': 0.28, 'A': 0.27}
    """
    from scripts.feature_engineering import compute_h2h

    if home_team not in team_stats or away_team not in team_stats:
        return {}

    hs = team_stats[home_team]
    as_ = team_stats[away_team]
    h2h = compute_h2h(df_hist, home_team, away_team)

    feat_cols = model_pkg['feat_cols']
    feat_map = {}

    stat_cols = [
        'home_win_rate', 'home_draw_rate', 'home_loss_rate',
        'home_goals_scored_avg', 'home_goals_conceded_avg',
        'away_win_rate', 'away_draw_rate', 'away_loss_rate',
        'away_goals_scored_avg', 'away_goals_conceded_avg',
        'overall_win_rate', 'recent_form', 'goal_diff_avg',
    ]
    for col in stat_cols:
        feat_map[f'h_{col}'] = hs.get(col, 0.0)
        feat_map[f'a_{col}'] = as_.get(col, 0.0)

    feat_map['diff_win_rate']         = hs['overall_win_rate'] - as_['overall_win_rate']
    feat_map['diff_recent_form']      = hs['recent_form']      - as_['recent_form']
    feat_map['diff_home_scored_avg']  = hs['home_goals_scored_avg']   - as_['away_goals_conceded_avg']
    feat_map['diff_away_concede_avg'] = as_['away_goals_scored_avg']  - hs['home_goals_conceded_avg']
    feat_map.update(h2h)

    X = np.array([[feat_map.get(c, 0.0) for c in feat_cols]])
    model = model_pkg['model']
    proba = model.predict_proba(X)[0]
    classes = model_pkg['classes']

    return {cls: round(float(p), 4) for cls, p in zip(classes, proba)}


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="训练足球比赛预测 ML 模型")
    parser.add_argument('--league', default=None, help='只用指定联赛训练 (如: 英超)')
    parser.add_argument('--output', default=MODEL_DIR, help='模型保存目录')
    parser.add_argument('--lookback', type=int, default=10, help='特征计算回看场次')
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 MatchPredict ML 训练管道")
    print("=" * 60)

    # Step 1: 加载数据
    print("\n📥 Step 1: 从数据库加载历史比赛...")
    df = load_historical_matches()
    if df.empty:
        print("❌ 没有可用的历史比赛，请先运行 sync_historical.py")
        sys.exit(1)
    print(f"   共 {len(df)} 场  |  联赛: {df['league_name'].dropna().unique().tolist()}")

    # Step 2: 计算球队统计
    print(f"\n⚙️  Step 2: 计算球队近期统计 (回看 {args.lookback} 场)...")
    team_stats = compute_team_stats(df, lookback=args.lookback)
    print(f"   共 {len(team_stats)} 支球队")

    # Step 3: 构建特征矩阵
    print("\n📐 Step 3: 构建特征矩阵...")
    feat_df = build_match_feature_matrix(df, team_stats)
    print(f"   特征矩阵: {feat_df.shape[0]} 场 × {feat_df.shape[1]} 列")

    # Step 4: 训练模型
    print("\n🤖 Step 4: 训练模型...")
    X, y, feat_cols, feat_df_filtered = prepare_xy(feat_df, league=args.league)
    print(f"   训练样本: {len(X)}  |  类别分布: H={sum(y=='H')}, D={sum(y=='D')}, A={sum(y=='A')}")

    model_pkg = train_models(X, y, feat_cols)

    # Step 5: 评估
    print("\n📊 Step 5: 模型评估...")
    evaluate_model(model_pkg, X, y, feat_df_filtered)

    # Step 6: 保存
    suffix = args.league.replace(' ', '_') if args.league else 'all'
    model_path = save_model(model_pkg, args.output, suffix)

    # Step 7: 更新球队评分表
    print("\n💾 Step 7: 写入球队统计到数据库...")
    save_team_ratings(team_stats)

    print("\n" + "=" * 60)
    print(f"🎉 训练完成！模型保存于: {model_path}")
    print(f"   CV 准确率: {model_pkg['cv_accuracy']:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
