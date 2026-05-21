# MatchPredict Pro ⚽

AI 驱动的足球比赛预测平台，融合 ML 模型 + AI 大模型深度分析。

## 快速开始

```bash
make install    # 安装依赖
make init-db    # 初始化数据库
make sync-all   # 全量同步数据 + 训练模型
make run        # 启动服务 → http://localhost:8000
```

## 核心功能

- **深度权重预测**：泊松分布 + 历史权重模型
- **AI 大模型分析**：调用 AI 生成深度研报
- **智能选场**：ML 模型自动推荐高置信度比赛
- **预测回顾**：预测 vs 真实结果逐场对比
- **世界杯预测**：2026 美加墨世界杯模拟

## 技术栈

- 后端：Flask + PostgreSQL + APScheduler
- ML：scikit-learn + XGBoost（CV 准确率 62%+）
- AI：OpenAI 兼容 API（可配置 OpenRouter/Gemini/360 等）
- 前端：原生 JS + CSS

## 数据管道

```
sync_historical → train_model → sync_upcoming → sync_results（每10分钟）
                                     ↓
                              sync_odds（the-odds-api）
```

## 目录结构

```
app.py              # Flask 主应用
scripts/
  database.py       # 数据库连接池 + ORM
  ai_predictor.py   # AI 调用模块（支持多服务商）
  feature_engineering.py  # 特征工程
  train_model.py    # ML 训练管道
  sync_upcoming.py  # 同步赛程 + 赔率 + ML 预测
  sync_results.py   # 同步比赛结果
  sync_historical.py # 同步历史数据
templates/          # Jinja2 模板
static/
  script.js         # 前端主脚本
  style.css         # 样式
models/             # 训练好的 ML 模型 (.pkl)
```

## 配置

复制 `.env.example` → `.env`，填写：
- `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASS`
- `FOOTBALL_DATA_API_KEY`（football-data.org）
- AI 模型配置在管理后台 `/profile`（admin 用户）动态管理

## 管理命令

```bash
make sync-history   # 同步历史数据
make train          # 重新训练模型
make sync-upcoming  # 同步未来赛程
make sync-results   # 同步比赛结果
make sync-all       # 全量同步
```
