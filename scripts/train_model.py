#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库驱动的 ML 训练管道（增强版）
1. 从数据库拉取历史比赛
2. 计算增强特征矩阵（含泊松、近期趋势、连胜连败）
3. 训练 RandomForest + GradientBoosting + XGBoost
4. 使用时间序列分割评估（避免未来数据泄露）
5. 评估并保存最佳模型
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
from sklearn.model_selection import StratifiedKFold, cross_val_score, TimeSeriesSplit
from sklearn.metrics import (accuracy_score, classification_report,
                              confusion_matrix, log_loss)
from sklearn.preprocessing import LabelEncoder
from sklearn.calibration import CalibratedClassifierCV

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    print("⚠️  xgboost 未安装，将跳过 XGBoost 模型。运行: pip install xgboost>=2.0.0")

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
    """准备 X（特征矩阵）和 y（标签），按 match_date 排序"""
    if league:
        feat_df = feat_df[feat_df['league'] == league].copy()
        logger.info(f"过滤联赛 [{league}] 后剩余 {len(feat_df)} 场")

    feat_df = feat_df.dropna(subset=['result'])
    
    # 按 match_date 排序（时间序列分割需要）
    if 'match_date' in feat_df.columns:
        feat_df = feat_df.sort_values('match_date').reset_index(drop=True)
    
    feat_cols = get_feature_cols(feat_df)
    X = feat_df[feat_cols].fillna(0).values
    y = feat_df['result'].values
    return X, y, feat_cols, feat_df


# ─────────────────────────────────────────────────────────────────────────────
# 模型训练（增强版：RF + GB + XGB + 时间序列分割）
# ─────────────────────────────────────────────────────────────────────────────

def train_models(X, y, feat_cols: list) -> dict:
    """训练三个模型并返回最优模型包（使用时间序列分割）"""
    
    # 使用时间序列分割（数据已按时间排序）
    tscv = TimeSeriesSplit(n_splits=5)
    # 同时保留 StratifiedKFold 作为对比
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    
    results = {}

    # ── RandomForest ──────────────────────────────────────────────────────────
    logger.info("🌲 训练 RandomForest ...")
    rf = RandomForestClassifier(
        n_estimators=500,
        max_depth=10,
        min_samples_leaf=5,
        min_samples_split=10,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1,
    )
    rf_ts_scores = cross_val_score(rf, X, y, cv=tscv, scoring='accuracy')
    rf_sk_scores = cross_val_score(rf, X, y, cv=skf, scoring='accuracy')
    logger.info(f"   RF TimeSeries CV: {rf_ts_scores.mean():.4f} ± {rf_ts_scores.std():.4f}")
    logger.info(f"   RF Stratified CV: {rf_sk_scores.mean():.4f} ± {rf_sk_scores.std():.4f}")
    rf.fit(X, y)
    results['RandomForest'] = {
        'model': rf,
        'ts_score': rf_ts_scores.mean(),
        'sk_score': rf_sk_scores.mean(),
        'ts_scores': rf_ts_scores.tolist(),
    }

    # ── GradientBoosting ──────────────────────────────────────────────────────
    logger.info("🚀 训练 GradientBoosting ...")
    gb = GradientBoostingClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=5,
        subsample=0.8,
        min_samples_leaf=5,
        random_state=42,
    )
    gb_ts_scores = cross_val_score(gb, X, y, cv=tscv, scoring='accuracy')
    gb_sk_scores = cross_val_score(gb, X, y, cv=skf, scoring='accuracy')
    logger.info(f"   GB TimeSeries CV: {gb_ts_scores.mean():.4f} ± {gb_ts_scores.std():.4f}")
    logger.info(f"   GB Stratified CV: {gb_sk_scores.mean():.4f} ± {gb_sk_scores.std():.4f}")
    gb.fit(X, y)
    results['GradientBoosting'] = {
        'model': gb,
        'ts_score': gb_ts_scores.mean(),
        'sk_score': gb_sk_scores.mean(),
        'ts_scores': gb_ts_scores.tolist(),
    }

    # ── XGBoost ───────────────────────────────────────────────────────────────
    if HAS_XGBOOST:
        logger.info("⚡ 训练 XGBoost ...")
        # 将标签编码为数字
        le = LabelEncoder()
        y_encoded = le.fit_transform(y)
        
        xgb = XGBClassifier(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=3,
            reg_alpha=0.1,
            reg_lambda=1.0,
            use_label_encoder=False,
            eval_metric='mlogloss',
            random_state=42,
            n_jobs=-1,
        )
        xgb_ts_scores = cross_val_score(xgb, X, y_encoded, cv=tscv, scoring='accuracy')
        xgb_sk_scores = cross_val_score(xgb, X, y_encoded, cv=skf, scoring='accuracy')
        logger.info(f"   XGB TimeSeries CV: {xgb_ts_scores.mean():.4f} ± {xgb_ts_scores.std():.4f}")
        logger.info(f"   XGB Stratified CV: {xgb_sk_scores.mean():.4f} ± {xgb_sk_scores.std():.4f}")
        xgb.fit(X, y_encoded)
        results['XGBoost'] = {
            'model': xgb,
            'ts_score': xgb_ts_scores.mean(),
            'sk_score': xgb_sk_scores.mean(),
            'ts_scores': xgb_ts_scores.tolist(),
            'label_encoder': le,
        }

    # ── 选最优（以 TimeSeries CV 为准，更接近实战）────────────────────────────
    best_name = max(results, key=lambda k: results[k]['ts_score'])
    best_score = results[best_name]['ts_score']
    best_model = results[best_name]['model']
    logger.info(f"🏆 最优模型: {best_name}  TimeSeries CV = {best_score:.4f}")

    # ── 校准概率（Platt scaling）─────────────────────────────────────────────
    # XGBoost 需要特殊处理（用编码后的标签）
    if best_name == 'XGBoost':
        le = results['XGBoost']['label_encoder']
        y_cal = le.transform(y)
        calibrated = CalibratedClassifierCV(best_model, method='sigmoid', cv=5)
        calibrated.fit(X, y_cal)
        classes = le.classes_.tolist()
    else:
        calibrated = CalibratedClassifierCV(best_model, method='sigmoid', cv=5)
        calibrated.fit(X, y)
        classes = best_model.classes_.tolist()

    return {
        'model':        calibrated,
        'base_model':   best_model,
        'model_name':   best_name,
        'cv_accuracy':  best_score,
        'sk_accuracy':  results[best_name]['sk_score'],
        'feat_cols':    feat_cols,
        'classes':      classes,
        'label_encoder': results.get('XGBoost', {}).get('label_encoder'),
        'all_results':  {k: {'ts_score': v['ts_score'], 'sk_score': v['sk_score']} for k, v in results.items()},
        'rf':           {'model': results['RandomForest']['model'], 'cv_scores': results['RandomForest']['ts_scores']},
        'gb':           {'model': results['GradientBoosting']['model'], 'cv_scores': results['GradientBoosting']['ts_scores']},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 模型评估
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_model(model_pkg: dict, X, y, feat_df: pd.DataFrame):
    """打印完整评估报告和特征重要性"""
    model = model_pkg['base_model']
    
    # XGBoost 需要编码标签
    if model_pkg['model_name'] == 'XGBoost' and model_pkg.get('label_encoder'):
        le = model_pkg['label_encoder']
        y_pred_encoded = model.predict(X)
        y_pred = le.inverse_transform(y_pred_encoded)
    else:
        y_pred = model.predict(X)
    
    acc = accuracy_score(y, y_pred)

    print("\n" + "=" * 60)
    print(f"📊 模型评估报告  ({model_pkg['model_name']})")
    print("=" * 60)
    print(f"训练集准确率: {acc:.4f}  ({acc*100:.1f}%)")
    print(f"TimeSeries CV 5折准确率: {model_pkg['cv_accuracy']:.4f}")
    print(f"Stratified CV 5折准确率: {model_pkg.get('sk_accuracy', 0):.4f}")
    
    # 打印所有模型对比
    if 'all_results' in model_pkg:
        print("\n📈 模型对比:")
        for name, scores in model_pkg['all_results'].items():
            marker = " 🏆" if name == model_pkg['model_name'] else ""
            print(f"  {name:<20} TS-CV: {scores['ts_score']:.4f}  SK-CV: {scores['sk_score']:.4f}{marker}")
    
    print()
    print(classification_report(y, y_pred, target_names=['客胜(A)', '平局(D)', '主胜(H)']))
    print("混淆矩阵 [A / D / H]:")
    print(confusion_matrix(y, y_pred, labels=['A', 'D', 'H']))

    # 特征重要性
    if hasattr(model, 'feature_importances_'):
        fi = pd.Series(model.feature_importances_, index=model_pkg['feat_cols'])
        fi_top = fi.nlargest(20)
        print("\n🔑 Top-20 重要特征:")
        for name, imp in fi_top.items():
            bar = '█' * int(imp * 100)
            print(f"  {name:<45} {imp:.4f}  {bar}")

    # 联赛分组准确率
    if 'league' in feat_df.columns:
        print("\n🌍 各联赛准确率:")
        for league, grp in feat_df.groupby('league'):
            feat_cols = model_pkg['feat_cols']
            X_l = grp[feat_cols].fillna(0).values
            y_l = grp['result'].values
            if model_pkg['model_name'] == 'XGBoost' and model_pkg.get('label_encoder'):
                le = model_pkg['label_encoder']
                y_pred_l = le.inverse_transform(model.predict(X_l))
            else:
                y_pred_l = model.predict(X_l)
            acc_l = accuracy_score(y_l, y_pred_l)
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
                           model_pkg: dict,
                           home_odds: float = None, draw_odds: float = None, away_odds: float = None) -> dict:
    """
    根据球队名和统计数据预测胜平负概率。
    如果提供了赔率，会加入赔率隐含概率特征以提高准确率。
    返回: {'H': 0.45, 'D': 0.28, 'A': 0.27, 'confidence': 'high/medium/low'}
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
        # 新增特征
        'recent_form_3', 'recent_form_5',
        'scoring_trend', 'conceding_trend',
        'home_attack_strength', 'home_defense_strength',
        'away_attack_strength', 'away_defense_strength',
        'win_streak', 'loss_streak', 'unbeaten_run',
    ]
    for col in stat_cols:
        feat_map[f'h_{col}'] = hs.get(col, 0.0)
        feat_map[f'a_{col}'] = as_.get(col, 0.0)

    # 差值特征
    feat_map['diff_win_rate']         = hs['overall_win_rate'] - as_['overall_win_rate']
    feat_map['diff_recent_form']      = hs['recent_form']      - as_['recent_form']
    feat_map['diff_home_scored_avg']  = hs['home_goals_scored_avg']   - as_['away_goals_conceded_avg']
    feat_map['diff_away_concede_avg'] = as_['away_goals_scored_avg']  - hs['home_goals_conceded_avg']
    
    # 新增差值特征
    feat_map['diff_scoring_trend']    = hs.get('scoring_trend', 0) - as_.get('scoring_trend', 0)
    feat_map['diff_conceding_trend']  = hs.get('conceding_trend', 0) - as_.get('conceding_trend', 0)
    feat_map['diff_form_3']           = hs.get('recent_form_3', 0) - as_.get('recent_form_3', 0)
    feat_map['diff_win_streak']       = hs.get('win_streak', 0) - as_.get('win_streak', 0)

    # 泊松期望进球
    league_home_avg = df_hist['home_score'].mean() if not df_hist.empty else 1.3
    league_away_avg = df_hist['away_score'].mean() if not df_hist.empty else 1.1
    home_attack = hs.get('home_attack_strength', 1.0)
    away_defense = as_.get('away_defense_strength', 1.0)
    away_attack = as_.get('away_attack_strength', 1.0)
    home_defense = hs.get('home_defense_strength', 1.0)
    feat_map['poisson_home_goals'] = round(home_attack * away_defense * league_home_avg, 4)
    feat_map['poisson_away_goals'] = round(away_attack * home_defense * league_away_avg, 4)
    feat_map['poisson_goal_diff']  = feat_map['poisson_home_goals'] - feat_map['poisson_away_goals']

    # H2H
    feat_map.update(h2h)

    # 赔率隐含概率特征
    if home_odds and draw_odds and away_odds:
        total = 1/home_odds + 1/draw_odds + 1/away_odds
        feat_map['odds_home_prob'] = round((1/home_odds) / total, 4)
        feat_map['odds_draw_prob'] = round((1/draw_odds) / total, 4)
        feat_map['odds_away_prob'] = round((1/away_odds) / total, 4)
        feat_map['odds_overround'] = round(total - 1, 4)
        max_prob = max(feat_map['odds_home_prob'], feat_map['odds_draw_prob'], feat_map['odds_away_prob'])
        feat_map['odds_favorite_overbet'] = 1 if max_prob > 0.65 else 0
        feat_map['has_odds'] = 1
    else:
        feat_map['odds_home_prob'] = 0.0
        feat_map['odds_draw_prob'] = 0.0
        feat_map['odds_away_prob'] = 0.0
        feat_map['odds_overround'] = 0.0
        feat_map['odds_favorite_overbet'] = 0
        feat_map['has_odds'] = 0

    X = np.array([[feat_map.get(c, 0.0) for c in feat_cols]])
    model = model_pkg['model']
    proba = model.predict_proba(X)[0]
    classes = model_pkg['classes']

    result = {cls: round(float(p), 4) for cls, p in zip(classes, proba)}
    
    # 置信度标记
    max_prob = max(result.values())
    if max_prob > 0.60:
        result['confidence'] = 'high'
    elif max_prob >= 0.45:
        result['confidence'] = 'medium'
    else:
        result['confidence'] = 'low'
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="训练足球比赛预测 ML 模型（增强版）")
    parser.add_argument('--league', default=None, help='只用指定联赛训练 (如: 英超)')
    parser.add_argument('--output', default=MODEL_DIR, help='模型保存目录')
    parser.add_argument('--lookback', type=int, default=10, help='特征计算回看场次')
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 MatchPredict ML 训练管道（增强版）")
    print("   模型: RF + GB + XGBoost | 评估: TimeSeriesSplit")
    print("=" * 60)

    # Step 1: 加载数据
    print("\n📥 Step 1: 从数据库加载历史比赛...")
    df = load_historical_matches()
    if df.empty:
        print("❌ 没有可用的历史比赛，请先运行 sync_historical.py")
        sys.exit(1)
    print(f"   共 {len(df)} 场  |  联赛: {df['league_name'].dropna().unique().tolist()}")

    # Step 2: 计算球队统计
    print(f"\n⚙️  Step 2: 计算球队增强统计 (回看 {args.lookback} 场)...")
    team_stats = compute_team_stats(df, lookback=args.lookback)
    print(f"   共 {len(team_stats)} 支球队")

    # Step 3: 构建特征矩阵（含赔率隐含概率）
    print("\n📐 Step 3: 构建增强特征矩阵...")
    # 尝试加载赔率数据
    odds_df = None
    try:
        from scripts.database import prediction_db
        with prediction_db.get_db_connection() as conn:
            odds_df = pd.read_sql("SELECT home_team, away_team, match_date, home_odds, draw_odds, away_odds FROM match_odds WHERE home_odds IS NOT NULL", conn)
        if odds_df is not None and not odds_df.empty:
            print(f"   💰 加载了 {len(odds_df)} 条赔率数据")
        else:
            print("   ⚠️ 无历史赔率数据（赔率特征将为 0，不影响训练）")
    except Exception as e:
        print(f"   ⚠️ 赔率数据加载失败: {e}")

    feat_df = build_match_feature_matrix(df, team_stats, odds_df=odds_df)
    print(f"   特征矩阵: {feat_df.shape[0]} 场 × {feat_df.shape[1]} 列")

    # Step 4: 训练模型
    print("\n🤖 Step 4: 训练模型（RF + GB + XGB）...")
    X, y, feat_cols, feat_df_filtered = prepare_xy(feat_df, league=args.league)
    print(f"   训练样本: {len(X)}  |  类别分布: H={sum(y=='H')}, D={sum(y=='D')}, A={sum(y=='A')}")
    print(f"   特征维度: {len(feat_cols)}")

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
    print(f"   最优模型: {model_pkg['model_name']}")
    print(f"   TimeSeries CV 准确率: {model_pkg['cv_accuracy']:.4f}")
    print(f"   Stratified CV 准确率: {model_pkg.get('sk_accuracy', 0):.4f}")
    if HAS_XGBOOST:
        print(f"   XGBoost: ✅ 已启用")
    else:
        print(f"   XGBoost: ❌ 未安装")
    print("=" * 60)


if __name__ == "__main__":
    main()
