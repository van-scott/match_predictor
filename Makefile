.PHONY: install run dev clean venv help init-db create-admin sync-history sync-upcoming train sync-all

# ── 工具链 ────────────────────────────────────────────────────────────────
PYTHON     := python3
VENV_DIR   := venv
PIP        := $(VENV_DIR)/bin/pip
VENV_PYTHON:= $(VENV_DIR)/bin/python

# ── 环境变量：优先读取 .env 文件，没有则用默认值 ─────────────────────────
# 使用方式：在 .env 中配置所有变量，make run 会自动加载
ENV_FILE := .env
ifneq (,$(wildcard $(ENV_FILE)))
  include $(ENV_FILE)
  export
endif

# 数据库（可被 .env 覆盖）
DB_HOST ?= 10.43.104.94
DB_PORT ?= 5432
DB_NAME ?= postgres
DB_USER ?= app
DB_PASS ?= so123!

# ── 导出给子进程 ──────────────────────────────────────────────────────────
export DB_HOST DB_PORT DB_NAME DB_USER DB_PASS
export GEMINI_API_KEY GEMINI_MODEL
export FOOTBALL_DATA_API_KEY SECRET_KEY

# ─────────────────────────────────────────────────────────────────────────
help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "快速开始："
	@echo "  1. 复制 .env.example → .env，填写 GEMINI_API_KEY"
	@echo "  2. make install      安装依赖"
	@echo "  3. make run          启动服务"

# ── 环境 ──────────────────────────────────────────────────────────────────
venv: ## 创建虚拟环境
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "📦 创建虚拟环境 $(VENV_DIR) ..."; \
		$(PYTHON) -m venv $(VENV_DIR); \
		echo "✅ 虚拟环境创建成功"; \
	else \
		echo "✅ 虚拟环境已存在，跳过创建"; \
	fi

install: venv ## 创建虚拟环境并安装所有依赖
	@echo "⬇️  安装依赖包..."
	@$(PIP) install --upgrade pip -q
	@$(PIP) install -r requirements.txt
	@echo "✅ 所有依赖安装完成！"
	@echo ""
	@echo "💡 下一步：复制 .env.example → .env 并填写配置，然后 make run"

env-check: ## 检查环境变量配置是否完整
	@echo "🔍 环境变量检查："
	@echo "   DB_HOST          = $(DB_HOST)"
	@echo "   DB_PORT          = $(DB_PORT)"
	@echo "   DB_NAME          = $(DB_NAME)"
	@echo "   DB_USER          = $(DB_USER)"
	@if [ -n "$(GEMINI_API_KEY)" ]; then \
		echo "   GEMINI_API_KEY   = ✅ 已设置 (前8位: $$(echo $(GEMINI_API_KEY) | cut -c1-8)...)"; \
	else \
		echo "   GEMINI_API_KEY   = ❌ 未设置（AI深度分析不可用，请在 .env 中配置）"; \
	fi
	@if [ -n "$(FOOTBALL_DATA_API_KEY)" ]; then \
		echo "   FOOTBALL_DATA_API= ✅ 已设置"; \
	else \
		echo "   FOOTBALL_DATA_API= ⚠️  使用默认值"; \
	fi

# ── 数据库 ─────────────────────────────────────────────────────────────────
init-db: ## 初始化数据库表结构 + 超管账号
	@echo "🗄️  初始化数据库..."
	@echo "   连接: $(DB_HOST):$(DB_PORT)/$(DB_NAME)"
	@if [ ! -f "$(VENV_PYTHON)" ]; then echo "❌ 请先执行 make install"; exit 1; fi
	@$(VENV_PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from scripts.database import prediction_db; \
prediction_db.init_tables(); \
prediction_db.ensure_credits_columns(); \
r = prediction_db.init_admin(); \
print('✅ 建表完成'); \
print('✅' if r['created'] else 'ℹ️ ', r['message'])"
	@echo "🎉 数据库初始化完成"

create-admin: ## 创建/重置超管账号（交互式）
	@$(VENV_PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from scripts.database import prediction_db; \
u = input('超管用户名 [admin]: ').strip() or 'admin'; \
e = input('超管邮箱 [admin@matchpro.com]: ').strip() or 'admin@matchpro.com'; \
p = input('初始密码 [admin888]: ').strip() or 'admin888'; \
r = prediction_db.init_admin(u, e, p); \
print('✅' if r['created'] else 'ℹ️ ', r['message'])"

# ── 启动 ──────────────────────────────────────────────────────────────────
run: env-check ## 启动 Flask 应用（自动加载 .env）
	@if [ ! -f "$(VENV_PYTHON)" ]; then echo "❌ 请先执行 make install"; exit 1; fi
	@echo "🚀 启动 MatchPredict... 访问 http://localhost:8000"
	@LOCAL_DEV=1 $(VENV_PYTHON) app.py

dev: ## 开发模式启动（热重载 + DEBUG）
	@if [ ! -f "$(VENV_PYTHON)" ]; then echo "❌ 请先执行 make install"; exit 1; fi
	@echo "🔧 开发模式启动..."
	@LOCAL_DEV=1 FLASK_ENV=development FLASK_DEBUG=1 $(VENV_PYTHON) app.py

# ── 数据管道 ───────────────────────────────────────────────────────────────
sync-history: ## 同步五大联赛历史比赛数据（约3500+场）
	@echo "📥 同步历史比赛数据..."
	@$(VENV_PYTHON) scripts/sync_historical.py --leagues PL,PD,SA,BL1,FL1 --seasons 2023,2024
	@echo "✅ 历史数据同步完成"

sync-upcoming: ## 同步未来14天赛程 + 批量 ML 预测
	@echo "🔄 同步未开赛赛程 + ML 预测..."
	@$(VENV_PYTHON) scripts/sync_upcoming.py --days 14
	@echo "✅ 赛程同步完成"

train: ## 训练 ML 预测模型（需先完成 sync-history）
	@echo "🤖 训练 ML 模型..."
	@$(VENV_PYTHON) scripts/train_model.py
	@echo "✅ 模型训练完成"

sync-all: sync-history train sync-upcoming ## 全量同步：历史数据 → 训练 → 未来赛程
	@echo "🎉 全量同步完成"

# ── 清理 ──────────────────────────────────────────────────────────────────
clean: ## 删除虚拟环境和缓存文件
	@echo "🧹 清理..."
	@rm -rf $(VENV_DIR)
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ 清理完成"
