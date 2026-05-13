.PHONY: install run dev clean venv help

# Python 解释器
PYTHON := python3
VENV_DIR := venv
PIP := $(VENV_DIR)/bin/pip
VENV_PYTHON := $(VENV_DIR)/bin/python

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "使用示例："
	@echo "  make install   # 一键创建虚拟环境并安装所有依赖"
	@echo "  make run       # 启动应用"

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
	@echo "💡 提示：运行 'make run' 启动应用"

init-db: ## 初始化数据库表结构 + 超管账号（直接连 PostgreSQL，无需 HTTP）
	@echo "🗄️  初始化数据库..."
	@echo "   使用连接: $(DB_HOST):$(DB_PORT)/$(DB_NAME)"
	@if [ ! -f "$(VENV_PYTHON)" ]; then \
		echo "❌ 请先执行 make install"; exit 1; \
	fi
	@$(VENV_PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from scripts.database import prediction_db; \
prediction_db.init_tables(); \
prediction_db.ensure_credits_columns(); \
r = prediction_db.init_admin(); \
print('✅ 建表完成'); \
print('✅' if r['created'] else 'ℹ️ ', r['message'])"
	@echo "🎉 数据库初始化完成"

create-admin: ## 创建/重置超管账号（交互式输入用户名和密码）
	@$(VENV_PYTHON) -c "\
import sys, hashlib; sys.path.insert(0, '.'); \
from scripts.database import prediction_db; \
u = input('超管用户名 [admin]: ').strip() or 'admin'; \
e = input('超管邮箱 [admin@matchpro.com]: ').strip() or 'admin@matchpro.com'; \
p = input('初始密码 [admin888]: ').strip() or 'admin888'; \
r = prediction_db.init_admin(u, e, p); \
print('✅' if r['created'] else 'ℹ️ ', r['message'])"


run: ## 启动 Flask 应用（使用虚拟环境）
	@if [ ! -f "$(VENV_PYTHON)" ]; then \
		echo "❌ 虚拟环境不存在，请先执行 make install"; \
		exit 1; \
	fi
	@echo "🚀 启动 MatchPredict..."
	@$(VENV_PYTHON) app.py

dev: ## 开发模式启动（热重载）
	@if [ ! -f "$(VENV_PYTHON)" ]; then \
		echo "❌ 虚拟环境不存在，请先执行 make install"; \
		exit 1; \
	fi
	@echo "🔧 开发模式启动..."
	@FLASK_ENV=development FLASK_DEBUG=1 $(VENV_PYTHON) app.py

clean: ## 删除虚拟环境和缓存文件
	@echo "🧹 清理..."
	@rm -rf $(VENV_DIR)
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ 清理完成"
