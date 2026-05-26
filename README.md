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

- **深度权重预测**：泊松分布 + 历史权重模型（CV 准确率 62%+）
- **AI 大模型分析**：调用 Gemini 生成深度研报
- **智能选场**：ML 模型自动推荐高置信度比赛
- **预测回顾**：预测 vs 真实结果逐场对比
- **世界杯预测**：2026 美加墨世界杯模拟
- **彩票模式**：接入中国体育彩票实时比赛数据

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Flask + PostgreSQL + APScheduler |
| ML | scikit-learn + XGBoost |
| AI | Gemini API（OpenAI 兼容接口，可切换 OpenRouter / 360 等） |
| 前端 | 原生 JS + CSS |
| 部署 | Vercel Serverless |

## 目录结构

```
app.py                          # Flask 主应用
scripts/
  database.py                   # 数据库连接池 + ORM
  ai_predictor.py               # AI 调用模块（支持多服务商）
  feature_engineering.py        # 特征工程
  train_model.py                # ML 训练管道
  sync_upcoming.py              # 同步赛程 + 赔率 + ML 预测
  sync_results.py               # 同步比赛结果
  sync_historical.py            # 同步历史数据
  sync_daily_matches.py         # 彩票模式每日同步
templates/                      # Jinja2 模板
static/
  script.js                     # 前端主脚本
  style.css                     # 样式
  js/                           # 模块化前端脚本
models/                         # 训练好的 ML 模型 (.pkl)
```

## 配置

复制 `.env.example` → `.env`：

```env
# 数据库
DB_HOST=localhost
DB_PORT=5432
DB_NAME=matchpredict
DB_USER=your_user
DB_PASS=your_password

# 数据 API
FOOTBALL_DATA_API_KEY=your_key   # football-data.org

# AI 模型（使用 AI 功能时必填）
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.0-flash-exp  # 可选，有默认值
```

> Vercel 部署：在项目 Settings → Environment Variables 中添加上述变量。

> 未设置 `GEMINI_API_KEY` 时，AI 预测不可用，但经典模式和彩票模式正常运行。

## 管理命令

```bash
make sync-history   # 同步历史数据
make train          # 重新训练模型
make sync-upcoming  # 同步未来赛程
make sync-results   # 同步比赛结果
make sync-all       # 全量同步
```

## 数据管道

```
sync_historical → train_model → sync_upcoming → sync_results（每10分钟）
                                     ↓
                              sync_odds（the-odds-api）
```

---

## 彩票模式数据同步

彩票模式**仅从数据库**读取数据，不调用外部 API，加载速度 < 50ms。

### 数据库表结构

```sql
CREATE TABLE daily_matches (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(100) UNIQUE NOT NULL,
    home_team VARCHAR(100) NOT NULL,
    away_team VARCHAR(100) NOT NULL,
    league_name VARCHAR(100),
    match_date DATE NOT NULL,
    match_time TIME,
    home_odds DECIMAL(6,2),
    draw_odds DECIMAL(6,2),
    away_odds DECIMAL(6,2),
    goal_line VARCHAR(10),
    data_source VARCHAR(50) DEFAULT 'china_lottery',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
```

### 同步命令

```bash
python scripts/sync_daily_matches.py --days 3      # 同步未来3天
python scripts/sync_daily_matches.py --days 7 --force  # 强制全量更新
python scripts/sync_daily_matches.py --stats       # 查看数据统计
python scripts/sync_daily_matches.py --cleanup 30  # 清理30天前数据
python scripts/sync_daily_matches.py --test        # 测试数据库连接
```

### 定时任务（生产环境）

```bash
0 8 * * *  cd /path/to/MatchPredict && python scripts/sync_daily_matches.py --days 3
0 2 * * 0  cd /path/to/MatchPredict && python scripts/sync_daily_matches.py --cleanup 30
```

### 故障排除

| 问题 | 解决方法 |
|---|---|
| 数据库连接失败 | 运行 `--test` 检查连接参数 |
| API 获取失败 | 检查网络，查看 `sync_matches.log` |
| 数据不一致 | 运行 `--days 7 --force` 强制重新同步 |

---

## AI 预测前端配置

AI 模式支持**直接在浏览器端**调用 Gemini API，无需后端中转。

### 配置 API 密钥（浏览器控制台）

```javascript
setGeminiApiKey("your_api_key_here")   // 推荐方式
```

其他方式：

```javascript
window.apiConfigManager.setApiKey("your_key")
localStorage.setItem("GEMINI_API_KEY", "your_key")
```

### 调试命令

```javascript
checkGeminiConfig()   // 查看配置状态
testGeminiAPI()       // 测试 API 连接
clearGeminiApiKey()   // 清除配置
```

> API 密钥仅存储在浏览器 localStorage 中，不会发送到服务器。

### 常见错误

| 错误 | 解决方法 |
|---|---|
| `请先配置GEMINI_API_KEY` | 运行 `setGeminiApiKey("your_key")` |
| `Gemini API调用失败: 403` | 检查密钥权限 |
| `网络错误` | 确认能访问 Google 服务 |

---

## 用户系统

### 用户类型

| 类型 | 每日预测次数 |
|---|---|
| 免费用户 | 3 次（每日重置） |
| 会员用户 | 无限制 |

### 认证 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/register` | 注册 |
| POST | `/api/login` | 登录（Session 有效期 7 天） |
| POST | `/api/logout` | 登出 |
| GET | `/api/user/info` | 获取当前用户信息 |
| GET | `/api/user/can-predict` | 检查是否还有预测次数 |

### 数据库结构

```sql
users (
    id, username, email, password_hash,
    user_type ('free'|'premium'), membership_expires,
    daily_predictions_used, last_prediction_date,
    total_predictions, created_at, last_login, is_active
)
```

### 安全措施

- 密码 SHA256 哈希存储
- Session 7 天有效期
- 前后端双重输入验证
- 参数化查询防 SQL 注入

---

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。
