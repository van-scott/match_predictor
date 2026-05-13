#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库管理模块
用于保存预测结果到PostgreSQL数据库
"""

import psycopg2
import psycopg2.extras
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import json
import contextlib # 导入 contextlib

# 配置日志
logger = logging.getLogger(__name__)

class PredictionDatabase:
    """预测结果数据库管理"""
    
    def __init__(self):
        logger.info("正在初始化数据库连接参数...")
        self.connection_params = {
            "host": os.getenv("DB_HOST", "dbprovider.ap-southeast-1.clawcloudrun.com"),
            "port": int(os.getenv("DB_PORT", "49674")),
            "database": os.getenv("DB_NAME", "postgres"),
            "user": os.getenv("DB_USER", "postgres"),
            "password": os.getenv("DB_PASS", "sbdx497p"), # 请务必在线上环境中设置此环境变量
            "sslmode": "prefer"
        }
        # self.init_tables() # 移除此行，数据库表的初始化应手动触发
    
    @contextlib.contextmanager
    def get_db_connection(self):
        """使用上下文管理器获取数据库连接，并处理事务。"""
        conn = None
        try:
            conn = psycopg2.connect(**self.connection_params)
            conn.autocommit = False # 禁用自动提交，手动管理事务
            logger.info("数据库连接成功并开始事务管理")
            yield conn
            conn.commit() # 成功时提交事务
            logger.info("事务提交成功")
        except Exception as e:
            if conn:
                conn.rollback() # 失败时回滚事务
                logger.error(f"数据库操作失败，事务已回滚: {e}")
            else:
                logger.error(f"数据库连接失败: {e}", exc_info=True)
            raise # 重新抛出异常，让上层处理
        finally:
            if conn:
                conn.close()
                logger.info("数据库连接已关闭")

    # 修改 connect_to_database 为 _get_conn，仅用于内部获取原始连接
    def _get_conn(self):
        """内部方法：直接获取原始数据库连接，不进行事务管理"""
        conn = None
        try:
            conn = psycopg2.connect(**self.connection_params)
            logger.debug("内部数据库连接成功")
            return conn
        except Exception as e:
            logger.error(f"内部数据库连接失败: {e}，参数: {self.connection_params.get('host')}:{self.connection_params.get('port')}/{self.connection_params.get('database')}", exc_info=True)
            if conn:
                conn.close()
            raise Exception(f"数据库连接失败: {e}")
    
    def init_tables(self):
        """
        初始化数据库表结构（幂等，可重复执行）。
        建议作为独立管理任务运行，而非应用启动时自动调用。

        表说明：
          users            — 用户主表，含角色 / 积分 / 签到 / 会员信息
          match_predictions — 预测记录表，关联用户，支持回填实际结果
          daily_matches     — 体彩每日比赛缓存表，由同步脚本定期写入
        """
        conn = None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # ── 1. 用户表 ────────────────────────────────────────────────────
            # user_type 取值：
            #   'free'    — 普通免费用户，每日预测次数有限，积分每日签到获取
            #   'premium' — 付费会员，积分获取翻倍，membership_expires 控制到期
            #   'admin'   — 超级管理员，不受次数 / 积分限制，可无限预测
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id                     SERIAL          PRIMARY KEY,
                    username               VARCHAR(50)     UNIQUE NOT NULL,   -- 用户名（唯一）
                    email                  VARCHAR(100)    UNIQUE NOT NULL,   -- 邮箱（唯一）
                    password_hash          VARCHAR(255)    NOT NULL,          -- SHA-256 密码哈希
                    user_type              VARCHAR(20)     NOT NULL DEFAULT 'free',
                                                                              -- 角色：free / premium / admin
                    membership_expires     DATE,                              -- 会员到期日（NULL=永久/非会员）
                    credits                INTEGER         NOT NULL DEFAULT 20,-- 当前积分余额
                    last_checkin_date      DATE,                              -- 最近一次签到日期（防重复签到）
                    daily_predictions_used INTEGER         NOT NULL DEFAULT 0, -- 今日已使用预测次数
                    last_prediction_date   DATE            DEFAULT CURRENT_DATE,-- 最近预测日期（用于每日重置）
                    total_predictions      INTEGER         NOT NULL DEFAULT 0, -- 累计预测总次数
                    created_at             TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login             TIMESTAMP,                         -- 最近登录时间
                    is_active              BOOLEAN         NOT NULL DEFAULT TRUE -- 账号是否启用
                );
            """)
            # 字段注释（PostgreSQL COMMENT ON COLUMN）
            for col, comment in [
                ("id",                     "用户主键，自增"),
                ("username",               "登录用户名，全局唯一"),
                ("email",                  "绑定邮箱，全局唯一"),
                ("password_hash",          "SHA-256 哈希密码，禁止明文存储"),
                ("user_type",              "用户角色：free=免费 / premium=会员 / admin=超管"),
                ("membership_expires",     "会员到期日；NULL 表示非会员或永久有效"),
                ("credits",                "积分余额：签到/充值增加，每次预测消耗"),
                ("last_checkin_date",      "最近签到日期，用于防止同日重复签到"),
                ("daily_predictions_used", "当日已使用预测次数，每日凌晨重置"),
                ("last_prediction_date",   "最近一次预测日期，用于触发每日次数重置"),
                ("total_predictions",      "历史累计预测总次数"),
                ("created_at",             "账号注册时间"),
                ("last_login",             "最近登录时间"),
                ("is_active",              "账号是否激活；FALSE 表示封禁/注销"),
            ]:
                cursor.execute(
                    f"COMMENT ON COLUMN users.{col} IS %s", (comment,)
                )
            cursor.execute("COMMENT ON TABLE users IS '用户主表：含角色、积分、签到、会员到期等信息';")

            # ── 2. 预测记录表 ────────────────────────────────────────────────
            # prediction_mode 取值：AI / Classic / Lottery / WorldCup
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_predictions (
                    id                    SERIAL          PRIMARY KEY,
                    prediction_id         VARCHAR(100)    UNIQUE NOT NULL,    -- 业务唯一ID（模式_队伍_时间戳）
                    user_id               INTEGER         REFERENCES users(id) ON DELETE SET NULL,
                    username              VARCHAR(50),                        -- 冗余字段，方便查询无需 JOIN
                    prediction_mode       VARCHAR(20)     NOT NULL,           -- 预测模式：AI/Classic/Lottery/WorldCup
                    home_team             VARCHAR(100)    NOT NULL,           -- 主队名称
                    away_team             VARCHAR(100)    NOT NULL,           -- 客队名称
                    league_name           VARCHAR(100),                       -- 联赛名称
                    match_time            TIMESTAMP,                          -- 比赛开始时间
                    home_odds             DECIMAL(6,2),                       -- 主胜赔率
                    draw_odds             DECIMAL(6,2),                       -- 平局赔率
                    away_odds             DECIMAL(6,2),                       -- 客胜赔率
                    predicted_result      VARCHAR(20),                        -- 预测结果：主胜/平局/客胜
                    prediction_confidence DECIMAL(5,2),                       -- 预测置信度 0-10
                    ai_analysis           TEXT,                               -- AI 分析全文
                    user_ip               VARCHAR(45),                        -- 用户 IP（IPv4/IPv6）
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    actual_result         VARCHAR(20),                        -- 实际比赛结果（回填）
                    actual_score          VARCHAR(20),                        -- 实际比分（回填）
                    is_correct            BOOLEAN,                            -- 预测是否正确（回填后计算）
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE match_predictions IS '预测记录表：记录每次预测详情，支持结果回填与准确率统计';")
            # 预测表字段注释
            for col, comment in [
                ("id", "预测主键，自增"),
                ("prediction_id", "业务唯一ID（模式_队伍_时间戳）"),
                ("user_id", "关联用户ID"),
                ("username", "冗余字段，方便查询无需 JOIN"),
                ("prediction_mode", "预测模式：AI/Classic/Lottery/WorldCup"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("league_name", "联赛名称"),
                ("match_time", "比赛开始时间"),
                ("home_odds", "主胜赔率"),
                ("draw_odds", "平局赔率"),
                ("away_odds", "客胜赔率"),
                ("predicted_result", "预测结果：主胜/平局/客胜"),
                ("prediction_confidence", "预测置信度 0-10"),
                ("ai_analysis", "AI 分析全文"),
                ("user_ip", "用户 IP（IPv4/IPv6）"),
                ("created_at", "预测创建时间"),
                ("actual_result", "实际比赛结果（回填）"),
                ("actual_score", "实际比分（回填）"),
                ("is_correct", "预测是否正确（回填后计算）"),
                ("updated_at", "最后更新时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN match_predictions.{col} IS %s", (comment,))

            # ── 3. 每日体彩比赛缓存表 ───────────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_matches (
                    id           SERIAL          PRIMARY KEY,
                    match_id     VARCHAR(100)    UNIQUE NOT NULL,             -- 体彩官方比赛编号
                    home_team    VARCHAR(100)    NOT NULL,                    -- 主队中文名
                    away_team    VARCHAR(100)    NOT NULL,                    -- 客队中文名
                    league_name  VARCHAR(100),                                -- 所属联赛
                    match_date   DATE            NOT NULL,                    -- 比赛日期
                    match_time   TIME,                                        -- 比赛时间（时分秒）
                    match_datetime TIMESTAMP,                                 -- 完整比赛时间戳
                    match_num    VARCHAR(20),                                 -- 期号/场次编号
                    match_status VARCHAR(20),                                 -- 状态：未开始/进行中/已结束
                    home_odds    DECIMAL(6,2),                                -- 主胜赔率（胜平负）
                    draw_odds    DECIMAL(6,2),                                -- 平局赔率
                    away_odds    DECIMAL(6,2),                                -- 客胜赔率
                    goal_line    VARCHAR(10),                                 -- 进球数大小球基准线
                    data_source  VARCHAR(50)     NOT NULL DEFAULT 'china_lottery',
                                                                             -- 数据来源：china_lottery / manual
                    created_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_active    BOOLEAN         NOT NULL DEFAULT TRUE        -- FALSE=已删除/下架
                );
            """)
            cursor.execute("COMMENT ON TABLE daily_matches IS '体彩每日比赛缓存表：由 sync_daily_matches.py 定期同步写入';")
            # 每日比赛表字段注释
            for col, comment in [
                ("id", "自增主键"),
                ("match_id", "体彩官方比赛编号，唯一"),
                ("home_team", "主队中文名"),
                ("away_team", "客队中文名"),
                ("league_name", "所属联赛"),
                ("match_date", "比赛日期"),
                ("match_time", "比赛时间（时分秒）"),
                ("match_datetime", "完整比赛时间戳"),
                ("match_num", "期号/场次编号"),
                ("match_status", "状态：未开始/进行中/已结束"),
                ("home_odds", "主胜赔率（胜平负）"),
                ("draw_odds", "平局赔率"),
                ("away_odds", "客胜赔率"),
                ("goal_line", "进球数大小球基准线"),
                ("data_source", "数据来源：china_lottery / manual"),
                ("created_at", "记录创建时间"),
                ("updated_at", "最后更新时间"),
                ("is_active", "FALSE=已删除/下架")
            ]:
                cursor.execute(f"COMMENT ON COLUMN daily_matches.{col} IS %s", (comment,))

            # ── 4. 历史比赛库 (historical_matches) ──────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historical_matches (
                    id                    SERIAL          PRIMARY KEY,
                    match_id              VARCHAR(100)    UNIQUE NOT NULL,
                    season                VARCHAR(20),
                    league_name           VARCHAR(100),
                    match_date            DATE,
                    match_time            TIME,
                    match_datetime        TIMESTAMP,
                    home_team             VARCHAR(100)    NOT NULL,
                    away_team             VARCHAR(100)    NOT NULL,
                    full_time_home_goals  INTEGER,
                    full_time_away_goals  INTEGER,
                    full_time_result      VARCHAR(10),
                    half_time_home_goals  INTEGER,
                    half_time_away_goals  INTEGER,
                    half_time_result      VARCHAR(10),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE historical_matches IS '历史比赛库：存储过去所有完赛的赛事详情，用于 AI 训练';")
            for col, comment in [
                ("id", "自增主键"),
                ("match_id", "业务唯一比赛ID"),
                ("season", "赛季（如 2023/24）"),
                ("league_name", "联赛名称"),
                ("match_date", "比赛日期"),
                ("match_time", "比赛时间"),
                ("match_datetime", "比赛完整时间戳"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("full_time_home_goals", "全场主队进球"),
                ("full_time_away_goals", "全场客队进球"),
                ("full_time_result", "全场赛果 (H/D/A)"),
                ("half_time_home_goals", "半场主队进球"),
                ("half_time_away_goals", "半场客队进球"),
                ("half_time_result", "半场赛果 (H/D/A)"),
                ("created_at", "记录创建时间"),
                ("updated_at", "记录最后更新时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN historical_matches.{col} IS %s", (comment,))

            # ── 5. 赔率库 (match_odds) ──────────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_odds (
                    id                    SERIAL          PRIMARY KEY,
                    match_id              VARCHAR(100),
                    home_team             VARCHAR(100)    NOT NULL,
                    away_team             VARCHAR(100)    NOT NULL,
                    match_date            DATE            NOT NULL,
                    bookmaker             VARCHAR(50)     NOT NULL,
                    home_odds             DECIMAL(8,3),
                    draw_odds             DECIMAL(8,3),
                    away_odds             DECIMAL(8,3),
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(home_team, away_team, match_date, bookmaker)
                );
            """)
            cursor.execute("COMMENT ON TABLE match_odds IS '赔率库：存储不同博彩公司的实时及初盘赔率';")
            for col, comment in [
                ("id", "自增主键"),
                ("match_id", "关联比赛ID"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("match_date", "比赛日期"),
                ("bookmaker", "博彩公司名称 (如 B365)"),
                ("home_odds", "主胜赔率"),
                ("draw_odds", "平局赔率"),
                ("away_odds", "客胜赔率"),
                ("updated_at", "赔率更新时间"),
                ("created_at", "赔率抓取时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN match_odds.{col} IS %s", (comment,))

            # ── 6. 球队战力评分 (team_ratings) ────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS team_ratings (
                    id                    SERIAL          PRIMARY KEY,
                    team_name             VARCHAR(100)    UNIQUE NOT NULL,
                    league_name           VARCHAR(100),
                    elo_rating            DECIMAL(10,2),
                    pi_rating             DECIMAL(10,2),
                    xg_for                DECIMAL(8,3),
                    xg_against            DECIMAL(8,3),
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE team_ratings IS '球队战力评分：基于 Elo, PI 等算法的战力评估值';")
            for col, comment in [
                ("id", "自增主键"),
                ("team_name", "球队名称"),
                ("league_name", "所属联赛"),
                ("elo_rating", "Elo 评分（基于比赛结果的等级分）"),
                ("pi_rating", "PI 评分（综合实力评分）"),
                ("xg_for", "平均预期进球（攻势水平）"),
                ("xg_against", "平均预期失球（防守水平）"),
                ("updated_at", "评分更新时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN team_ratings.{col} IS %s", (comment,))

            # ── 7. 比赛特征表 (match_features) ────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_features (
                    match_id              VARCHAR(100)    PRIMARY KEY,
                    form_home             JSONB,
                    form_away             JSONB,
                    h2h                   JSONB,
                    xg_home               DECIMAL(8,3),
                    xg_away               DECIMAL(8,3),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE match_features IS '比赛特征表：存储清洗加工后的、可直接喂给 AI 的特征向量';")
            for col, comment in [
                ("match_id", "唯一比赛ID"),
                ("form_home", "主队近期状态 (JSON 格式)"),
                ("form_away", "客队近期状态 (JSON 格式)"),
                ("h2h", "历史交锋数据 (JSON 格式)"),
                ("xg_home", "本场主队预期进球"),
                ("xg_away", "本场客队预期进球"),
                ("created_at", "特征计算生成时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN match_features.{col} IS %s", (comment,))

            # ── 8. AI 分析结果 (analysis_results) ─────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id                    SERIAL          PRIMARY KEY,
                    match_id              VARCHAR(100)    NOT NULL,
                    analysis_text         TEXT,
                    win_prob              DECIMAL(5,4),
                    draw_prob             DECIMAL(5,4),
                    lose_prob             DECIMAL(5,4),
                    recommendation        VARCHAR(100),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE analysis_results IS 'AI 分析结果：存储模型跑批后的概率预测与文字建议';")
            for col, comment in [
                ("id", "自增主键"),
                ("match_id", "关联比赛ID"),
                ("analysis_text", "AI 生成的详细文字分析"),
                ("win_prob", "胜概率 (0.0 - 1.0)"),
                ("draw_prob", "平概率"),
                ("lose_prob", "负概率"),
                ("recommendation", "推荐方案 (如: 胜、让胜)"),
                ("created_at", "分析生成时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN analysis_results.{col} IS %s", (comment,))

            # ── 9. 未开赛程 (upcoming_fixtures) ──────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upcoming_fixtures (
                    id                    SERIAL          PRIMARY KEY,
                    fixture_id            VARCHAR(100)    UNIQUE NOT NULL,
                    league_name           VARCHAR(100),
                    home_team             VARCHAR(100)    NOT NULL,
                    away_team             VARCHAR(100)    NOT NULL,
                    match_time            TIMESTAMP,
                    status                VARCHAR(20)     DEFAULT 'NS',
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE upcoming_fixtures IS '未开赛程：存储即将进行的、尚未有结果的赛事排期';")
            for col, comment in [
                ("id", "自增主键"),
                ("fixture_id", "赛程ID"),
                ("league_name", "联赛名称"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("match_time", "开赛时间"),
                ("status", "状态 (NS=未开始)"),
                ("updated_at", "记录更新时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN upcoming_fixtures.{col} IS %s", (comment,))

            # ── 10. 日志系统 (sync_log / update_log) ──────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_log (
                    id                    SERIAL          PRIMARY KEY,
                    sync_time             TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    matches_count         INTEGER         DEFAULT 0,
                    status                VARCHAR(20),
                    error_message         TEXT
                );
                CREATE TABLE IF NOT EXISTS data_update_log (
                    id                    SERIAL          PRIMARY KEY,
                    module_name           VARCHAR(50),
                    data_type             VARCHAR(50),
                    records_updated       INTEGER,
                    started_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at          TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE sync_log IS '数据同步日志表';")
            for col, comment in [
                ("id", "自增主键"),
                ("sync_time", "同步执行时间"),
                ("matches_count", "更新比赛数量"),
                ("status", "状态 (Success/Failed)"),
                ("error_message", "错误详情")
            ]:
                cursor.execute(f"COMMENT ON COLUMN sync_log.{col} IS %s", (comment,))

            cursor.execute("COMMENT ON TABLE data_update_log IS '模块数据更新细化日志';")
            for col, comment in [
                ("id", "自增主键"),
                ("module_name", "所属模块 (如: Crawler, AI)"),
                ("data_type", "数据类型 (如: Odds, Fixtures)"),
                ("records_updated", "更新条数"),
                ("started_at", "开始时间"),
                ("completed_at", "结束时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN data_update_log.{col} IS %s", (comment,))

            # ── 11. 其他辅助表 (articles / matches / league_standings) ──────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id                    SERIAL          PRIMARY KEY,
                    title                 VARCHAR(255)    NOT NULL,
                    content               TEXT,
                    tags                  VARCHAR(255),
                    pdf_url               TEXT,
                    arxiv_id              VARCHAR(50),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS matches (
                    match_id              VARCHAR(100)    PRIMARY KEY,
                    match_num             VARCHAR(20),
                    league_name           VARCHAR(100),
                    home_team             VARCHAR(100)    NOT NULL,
                    away_team             VARCHAR(100)    NOT NULL,
                    match_date            DATE,
                    match_time            TIME,
                    status                VARCHAR(20),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS league_standings (
                    id                    SERIAL          PRIMARY KEY,
                    league_name           VARCHAR(100)    NOT NULL,
                    season                VARCHAR(20)     NOT NULL,
                    team_name             VARCHAR(100)    NOT NULL,
                    position              INTEGER,
                    played                INTEGER,
                    points                INTEGER,
                    updated_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE articles IS '文章分析库：存储模型参考资料或分析文章';")
            for col, comment in [
                ("id", "自增主键"),
                ("title", "文章标题"),
                ("content", "内容全文"),
                ("tags", "标签集合"),
                ("pdf_url", "PDF 链接"),
                ("arxiv_id", "Arxiv 文献 ID"),
                ("created_at", "收录时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN articles.{col} IS %s", (comment,))

            cursor.execute("COMMENT ON TABLE matches IS '全量赛事索引表';")
            for col, comment in [
                ("match_id", "全局唯一比赛ID"),
                ("match_num", "场次编号/期号"),
                ("league_name", "联赛名称"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("match_date", "比赛日期"),
                ("match_time", "比赛时间"),
                ("status", "当前状态"),
                ("created_at", "记录创建时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN matches.{col} IS %s", (comment,))

            cursor.execute("COMMENT ON TABLE league_standings IS '联赛积分榜';")
            for col, comment in [
                ("id", "自增主键"),
                ("league_name", "联赛名称"),
                ("season", "赛季"),
                ("team_name", "球队名称"),
                ("position", "当前排名"),
                ("played", "已赛场次"),
                ("points", "积分"),
                ("updated_at", "更新时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN league_standings.{col} IS %s", (comment,))

            # ── 12. 外部战力数据 (club_elo_ratings / club_matches) ───────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS club_elo_ratings (
                    id                    SERIAL          PRIMARY KEY,
                    club                  VARCHAR(100)    NOT NULL,
                    country               VARCHAR(50),
                    elo_rating            DECIMAL(10,2),
                    snapshot_date         DATE            NOT NULL,
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS club_matches (
                    id                    SERIAL          PRIMARY KEY,
                    match_date            DATE            NOT NULL,
                    home_team             VARCHAR(100)    NOT NULL,
                    away_team             VARCHAR(100)    NOT NULL,
                    ft_home_goals         INTEGER,
                    ft_away_goals         INTEGER,
                    ft_result             VARCHAR(10),
                    created_at            TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("COMMENT ON TABLE club_elo_ratings IS '外部俱乐部 Elo 等级分记录';")
            for col, comment in [
                ("id", "自增主键"),
                ("club", "俱乐部名称"),
                ("country", "所属国家"),
                ("elo_rating", "Elo 评分"),
                ("snapshot_date", "快照日期"),
                ("created_at", "创建时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN club_elo_ratings.{col} IS %s", (comment,))

            cursor.execute("COMMENT ON TABLE club_matches IS '外部俱乐部历史比赛详情';")
            for col, comment in [
                ("id", "自增主键"),
                ("match_date", "比赛日期"),
                ("home_team", "主队名称"),
                ("away_team", "客队名称"),
                ("ft_home_goals", "全场主进球"),
                ("ft_away_goals", "全场客进球"),
                ("ft_result", "全场赛果"),
                ("created_at", "创建时间")
            ]:
                cursor.execute(f"COMMENT ON COLUMN club_matches.{col} IS %s", (comment,))

            # ── 13. 索引 ─────────────────────────────────────────────────────
            indexes = [
                # users
                "CREATE INDEX IF NOT EXISTS idx_users_type     ON users(user_type);",
                "CREATE INDEX IF NOT EXISTS idx_users_active   ON users(is_active);",
                # match_predictions
                "CREATE INDEX IF NOT EXISTS idx_pred_user      ON match_predictions(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_pred_mode      ON match_predictions(prediction_mode);",
                "CREATE INDEX IF NOT EXISTS idx_pred_created   ON match_predictions(created_at);",
                "CREATE INDEX IF NOT EXISTS idx_pred_teams     ON match_predictions(home_team, away_team);",
                "CREATE INDEX IF NOT EXISTS idx_pred_correct   ON match_predictions(is_correct);",
                # daily_matches
                "CREATE INDEX IF NOT EXISTS idx_dm_date        ON daily_matches(match_date);",
                "CREATE INDEX IF NOT EXISTS idx_dm_teams       ON daily_matches(home_team, away_team);",
                "CREATE INDEX IF NOT EXISTS idx_dm_league      ON daily_matches(league_name);",
                "CREATE INDEX IF NOT EXISTS idx_dm_status      ON daily_matches(match_status);",
                "CREATE INDEX IF NOT EXISTS idx_dm_active      ON daily_matches(is_active);",
                "CREATE INDEX IF NOT EXISTS idx_dm_datetime    ON daily_matches(match_datetime);",
                # historical_matches
                "CREATE INDEX IF NOT EXISTS idx_hist_dt        ON historical_matches(match_datetime);",
                "CREATE INDEX IF NOT EXISTS idx_hist_teams     ON historical_matches(home_team, away_team);",
                # match_odds
                "CREATE INDEX IF NOT EXISTS idx_odds_date      ON match_odds(match_date);",
                "CREATE INDEX IF NOT EXISTS idx_odds_teams     ON match_odds(home_team, away_team);",
                # upcoming_fixtures
                "CREATE INDEX IF NOT EXISTS idx_up_time        ON upcoming_fixtures(match_time);",
            ]
            for sql in indexes:
                cursor.execute(sql)

            conn.commit()
            cursor.close()
            logger.info("✅ 数据库表初始化成功（含注释 & 索引）")

        except Exception as e:
            logger.error(f"数据库表初始化失败: {e}", exc_info=True)
            if conn:
                conn.rollback()
            raise Exception(f"数据库初始化失败: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def init_admin(self, username: str = 'admin', email: str = 'admin@matchpro.com',
                   password: str = 'admin888') -> dict:
        """
        初始化超级管理员账号（幂等，已存在则跳过）。

        超管特性：
          - user_type = 'admin'
          - credits = 999999（近似无限）
          - 不受每日次数限制（can_user_predict 对 admin 恒返回 True）
          - 世界杯预测 / 积分消耗接口跳过扣费

        Args:
            username: 管理员用户名，默认 'admin'
            email:    管理员邮箱，默认 'admin@matchpro.com'
            password: 初始明文密码（写入时会被哈希），默认 'admin888'

        Returns:
            {'created': True/False, 'username': ..., 'message': ...}
        """
        import hashlib
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()

                # 检查是否已存在同名 admin
                cursor.execute(
                    "SELECT id, username FROM users WHERE username = %s OR (user_type = 'admin' AND email = %s)",
                    (username, email)
                )
                existing = cursor.fetchone()
                if existing:
                    logger.info(f"超管账号已存在，跳过创建: {existing[1]}")
                    return {
                        'created': False,
                        'username': existing[1],
                        'message': f'超管账号 [{existing[1]}] 已存在，未重复创建'
                    }

                cursor.execute(
                    """
                    INSERT INTO users
                        (username, email, password_hash, user_type, credits,
                         membership_expires, is_active)
                    VALUES
                        (%s, %s, %s, 'admin', 999999, NULL, TRUE)
                    """,
                    (username, email, password_hash)
                )
                logger.info(f"✅ 超管账号创建成功: {username}")
                return {
                    'created': True,
                    'username': username,
                    'message': f'超管账号 [{username}] 创建成功，初始密码已设置'
                }
        except Exception as e:
            logger.error(f"创建超管账号失败: {e}", exc_info=True)
            return {'created': False, 'username': username, 'message': str(e)}
    
    def save_prediction(self, prediction_data: Dict[str, Any]) -> bool:
        """
        保存预测结果到数据库
        
        Args:
            prediction_data: 预测数据字典
            
        Returns:
            保存是否成功
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # 准备插入数据
                insert_sql = """
            INSERT INTO match_predictions (
                prediction_id, prediction_mode, home_team, away_team, league_name,
                match_time, home_odds, draw_odds, away_odds, predicted_result,
                prediction_confidence, ai_analysis, user_ip
            ) VALUES (
                %(prediction_id)s, %(prediction_mode)s, %(home_team)s, %(away_team)s, %(league_name)s,
                %(match_time)s, %(home_odds)s, %(draw_odds)s, %(away_odds)s, %(predicted_result)s,
                %(prediction_confidence)s, %(ai_analysis)s, %(user_ip)s
            ) ON CONFLICT (prediction_id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                predicted_result = EXCLUDED.predicted_result,
                prediction_confidence = EXCLUDED.prediction_confidence,
                ai_analysis = EXCLUDED.ai_analysis;
            """
            
            cursor.execute(insert_sql, prediction_data)
            # conn.commit() # 由上下文管理器处理
            cursor.close()
            # conn.close() # 由上下文管理器处理
            
            logger.info(f"预测结果保存成功: {prediction_data.get('prediction_id')}")
            return True
            
        except Exception as e:
            logger.error(f"保存预测结果失败: {e}")
            # if conn: # 由上下文管理器处理
            #     conn.rollback() # 确保事务回滚
            #     conn.close()
            return False
    
    def save_ai_prediction(self, match_data: Dict[str, Any], prediction_result: str, 
                          confidence: float, ai_analysis: str, user_ip: str = None,
                          user_id: int = None, username: str = None) -> bool:
        """
        保存AI模式预测结果
        
        Args:
            match_data: 比赛数据
            prediction_result: 预测结果 (主胜/平局/客胜)
            confidence: 预测信心指数 (0-10)
            ai_analysis: AI分析内容
            user_ip: 用户IP
            
        Returns:
            保存是否成功
        """
        try:
            # 提取赔率信息
            odds = match_data.get('odds', {})
            
            # 生成预测ID
            prediction_id = f"ai_{match_data.get('home_team', '')}_{match_data.get('away_team', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # 解析比赛时间
            match_time = None
            if match_data.get('match_time'):
                try:
                    match_time = datetime.strptime(match_data['match_time'], '%Y-%m-%d %H:%M:%S')
                except:
                    try:
                        match_time = datetime.strptime(match_data['match_time'], '%Y-%m-%d %H:%M')
                    except:
                        pass
            
            prediction_data = {
                'prediction_id': prediction_id,
                'prediction_mode': 'AI',
                'user_id': user_id,
                'username': username,
                'home_team': match_data.get('home_team', ''),
                'away_team': match_data.get('away_team', ''),
                'league_name': match_data.get('league_name', ''),
                'match_time': match_time,
                'home_odds': float(odds.get('home_odds', 0)) if odds.get('home_odds') else None,
                'draw_odds': float(odds.get('draw_odds', 0)) if odds.get('draw_odds') else None,
                'away_odds': float(odds.get('away_odds', 0)) if odds.get('away_odds') else None,
                'predicted_result': prediction_result,
                'prediction_confidence': confidence,
                'ai_analysis': ai_analysis,
                'user_ip': user_ip or 'unknown'
            }
            
            return self.save_prediction(prediction_data)
            
        except Exception as e:
            logger.error(f"保存AI预测失败: {e}")
            return False
    
    def save_classic_prediction(self, match_data: Dict[str, Any], prediction_result: str, 
                               confidence: float, user_ip: str = None,
                               user_id: int = None, username: str = None) -> bool:
        """
        保存经典模式预测结果
        
        Args:
            match_data: 比赛数据
            prediction_result: 预测结果
            confidence: 预测信心指数
            user_ip: 用户IP
            
        Returns:
            保存是否成功
        """
        try:
            prediction_id = f"classic_{match_data.get('home_team', '')}_{match_data.get('away_team', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            prediction_data = {
                'prediction_id': prediction_id,
                'prediction_mode': 'Classic',
                'user_id': user_id,
                'username': username,
                'home_team': match_data.get('home_team', ''),
                'away_team': match_data.get('away_team', ''),
                'league_name': match_data.get('league_name', ''),
                'match_time': None,
                'home_odds': float(match_data.get('home_odds', 0)) if match_data.get('home_odds') else None,
                'draw_odds': float(match_data.get('draw_odds', 0)) if match_data.get('draw_odds') else None,
                'away_odds': float(match_data.get('away_odds', 0)) if match_data.get('away_odds') else None,
                'predicted_result': prediction_result,
                'prediction_confidence': confidence,
                'ai_analysis': '经典模式预测',
                'user_ip': user_ip or 'unknown'
            }
            
            return self.save_prediction(prediction_data)
            
        except Exception as e:
            logger.error(f"保存经典预测失败: {e}")
            return False
    
    def save_lottery_prediction(self, match_data: Dict[str, Any], prediction_result: str, 
                               confidence: float, ai_analysis: str, user_ip: str = None,
                               user_id: int = None, username: str = None) -> bool:
        """
        保存彩票模式预测结果
        
        Args:
            match_data: 比赛数据
            prediction_result: 预测结果
            confidence: 预测信心指数
            ai_analysis: AI分析内容
            user_ip: 用户IP
            
        Returns:
            保存是否成功
        """
        try:
            # 提取赔率信息
            odds = match_data.get('odds', {})
            hhad_odds = odds.get('hhad', {})
            
            prediction_id = f"lottery_{match_data.get('match_id', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # 解析比赛时间
            match_time = None
            if match_data.get('match_time'):
                try:
                    match_time = datetime.strptime(match_data['match_time'], '%Y-%m-%d %H:%M:%S')
                except:
                    try:
                        match_time = datetime.strptime(match_data['match_time'], '%Y-%m-%d %H:%M')
                    except:
                        pass
            
            prediction_data = {
                'prediction_id': prediction_id,
                'prediction_mode': 'Lottery',
                'user_id': user_id,
                'username': username,
                'home_team': match_data.get('home_team', ''),
                'away_team': match_data.get('away_team', ''),
                'league_name': match_data.get('league_name', ''),
                'match_time': match_time,
                'home_odds': float(hhad_odds.get('h', 0)) if hhad_odds.get('h') else None,
                'draw_odds': float(hhad_odds.get('d', 0)) if hhad_odds.get('d') else None,
                'away_odds': float(hhad_odds.get('a', 0)) if hhad_odds.get('a') else None,
                'predicted_result': prediction_result,
                'prediction_confidence': confidence,
                'ai_analysis': ai_analysis,
                'user_ip': user_ip or 'unknown'
            }
            
            return self.save_prediction(prediction_data)
            
        except Exception as e:
            logger.error(f"保存彩票预测失败: {e}")
            return False
    
    def get_prediction_stats(self) -> Dict[str, Any]:
        """
        获取预测统计信息
        
        Returns:
            统计信息字典
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                # 总体统计
                stats_sql = """
            SELECT 
                prediction_mode,
                COUNT(*) as total_predictions,
                COUNT(CASE WHEN is_correct = true THEN 1 END) as correct_predictions,
                ROUND(AVG(prediction_confidence), 2) as avg_confidence
            FROM match_predictions 
            GROUP BY prediction_mode
            ORDER BY prediction_mode;
            """
                
                cursor.execute(stats_sql)
                mode_stats = cursor.fetchall()
                
                # 最近预测
                recent_sql = """
            SELECT home_team, away_team, predicted_result, is_correct, created_at
            FROM match_predictions 
            ORDER BY created_at DESC 
            LIMIT 10;
            """
                
                cursor.execute(recent_sql)
                recent_predictions = cursor.fetchall()
                
                cursor.close()
                # conn.close() # 由上下文管理器处理
                
                return {
                    'mode_stats': [dict(row) for row in mode_stats],
                    'recent_predictions': [dict(row) for row in recent_predictions]
                }
                
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {'mode_stats': [], 'recent_predictions': []}
    
    def save_daily_matches(self, matches_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        保存每日比赛数据到数据库
        
        Args:
            matches_data: 比赛数据列表
            
        Returns:
            统计信息字典 {'inserted': 插入数量, 'updated': 更新数量, 'skipped': 跳过数量}
        """
        stats = {'inserted': 0, 'updated': 0, 'skipped': 0}
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                for match in matches_data:
                    try:
                        # 解析比赛时间
                        match_datetime = None
                        match_date = None
                        match_time = None
                        
                        if match.get('match_time'):
                            try:
                                match_datetime = datetime.strptime(match['match_time'], '%Y-%m-%d %H:%M:%S')
                                match_date = match_datetime.date()
                                match_time = match_datetime.time()
                            except:
                                try:
                                    match_datetime = datetime.strptime(match['match_time'], '%Y-%m-%d %H:%M')
                                    match_date = match_datetime.date()
                                    match_time = match_datetime.time()
                                except:
                                    if match.get('match_date'):
                                        match_date = datetime.strptime(match['match_date'], '%Y-%m-%d').date()
                        
                        # 提取赔率
                        odds = match.get('odds', {})
                        hhad_odds = odds.get('hhad', {})
                        
                        # 检查是否已存在
                        check_sql = "SELECT id FROM daily_matches WHERE match_id = %s"
                        cursor.execute(check_sql, (match.get('match_id'),))
                        existing = cursor.fetchone()
                        
                        if existing:
                            # 更新现有记录
                            update_sql = """
                        UPDATE daily_matches SET
                            home_team = %s,
                            away_team = %s,
                            league_name = %s,
                            match_date = %s,
                            match_time = %s,
                            match_datetime = %s,
                            match_num = %s,
                            match_status = %s,
                            home_odds = %s,
                            draw_odds = %s,
                            away_odds = %s,
                            goal_line = %s,
                            data_source = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE match_id = %s
                        """
                            
                            cursor.execute(update_sql, (
                                match.get('home_team', ''),
                                match.get('away_team', ''),
                                match.get('league_name', ''),
                                match_date,
                                match_time,
                                match_datetime,
                                match.get('match_num', ''),
                                match.get('status', ''),
                                float(hhad_odds.get('h', 0)) if hhad_odds.get('h') else None,
                                float(hhad_odds.get('d', 0)) if hhad_odds.get('d') else None,
                                float(hhad_odds.get('a', 0)) if hhad_odds.get('a') else None,
                                odds.get('goal_line', ''),
                                match.get('source', 'china_lottery'),
                                match.get('match_id')
                            ))
                            stats['updated'] += 1
                            
                        else:
                            # 插入新记录
                            insert_sql = """
                        INSERT INTO daily_matches (
                            match_id, home_team, away_team, league_name,
                            match_date, match_time, match_datetime, match_num,
                            match_status, home_odds, draw_odds, away_odds,
                            goal_line, data_source
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """
                            
                            cursor.execute(insert_sql, (
                                match.get('match_id', ''),
                                match.get('home_team', ''),
                                match.get('away_team', ''),
                                match.get('league_name', ''),
                                match_date,
                                match_time,
                                match_datetime,
                                match.get('match_num', ''),
                                match.get('status', ''),
                                float(hhad_odds.get('h', 0)) if hhad_odds.get('h') else None,
                                float(hhad_odds.get('d', 0)) if hhad_odds.get('d') else None,
                                float(hhad_odds.get('a', 0)) if hhad_odds.get('a') else None,
                                odds.get('goal_line', ''),
                                match.get('source', 'china_lottery')
                            ))
                            stats['inserted'] += 1
                            
                    except Exception as match_error:
                        logger.warning(f"保存单场比赛失败: {match_error}")
                        stats['skipped'] += 1
                        continue
                
                # conn.commit() # 由上下文管理器处理
                cursor.close()
                # conn.close() # 由上下文管理器处理
                
                logger.info(f"每日比赛数据保存完成 - 新增:{stats['inserted']}, 更新:{stats['updated']}, 跳过:{stats['skipped']}")
                return stats
                
        except Exception as e:
            logger.error(f"保存每日比赛数据失败: {e}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.rollback()
            #     conn.close()
            return stats
    
    def get_daily_matches(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        从数据库获取每日比赛数据
        
        Args:
            days_ahead: 未来天数
            
        Returns:
            比赛数据列表
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                # 计算日期范围
                today = datetime.now().date()
                end_date = today + timedelta(days=days_ahead)
                
                query_sql = """
            SELECT 
                match_id, home_team, away_team, league_name,
                match_date, match_time, match_datetime, match_num,
                match_status, home_odds, draw_odds, away_odds,
                goal_line, data_source, updated_at
            FROM daily_matches 
            WHERE match_date >= %s AND match_date <= %s 
            AND is_active = TRUE
            ORDER BY match_datetime ASC, match_date ASC, match_time ASC
            """
                
                cursor.execute(query_sql, (today, end_date))
                results = cursor.fetchall()
                
                # 转换为标准格式
                matches = []
                for row in results:
                    match_time_str = ''
                    if row['match_datetime']:
                        match_time_str = row['match_datetime'].strftime('%Y-%m-%d %H:%M:%S')
                    elif row['match_date'] and row['match_time']:
                        match_time_str = f"{row['match_date']} {row['match_time']}"
                    elif row['match_date']:
                        match_time_str = str(row['match_date'])
                    
                    match_data = {
                        'match_id': row['match_id'],
                        'home_team': row['home_team'],
                        'away_team': row['away_team'],
                        'league_name': row['league_name'],
                        'match_time': match_time_str,
                        'match_date': str(row['match_date']) if row['match_date'] else '',
                        'match_num': row['match_num'],
                        'status': row['match_status'],
                        'source': 'database',
                        'odds': {
                            'hhad': {
                                'h': str(row['home_odds']) if row['home_odds'] else '0',
                                'd': str(row['draw_odds']) if row['draw_odds'] else '0',
                                'a': str(row['away_odds']) if row['away_odds'] else '0'
                            },
                            'goal_line': row['goal_line']
                        }
                    }
                    matches.append(match_data)
                
                cursor.close()
                # conn.close() # 由上下文管理器处理
                
                logger.info(f"从数据库获取 {len(matches)} 场比赛")
                return matches
                
        except Exception as e:
            logger.error(f"从数据库获取比赛数据失败: {e}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.close()
            return []
    
    def cleanup_old_matches(self, days_to_keep: int = 30) -> int:
        """
        清理旧的比赛数据
        
        Args:
            days_to_keep: 保留天数
            
        Returns:
            删除的记录数
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                cutoff_date = datetime.now().date() - timedelta(days=days_to_keep)
                
                delete_sql = """
            DELETE FROM daily_matches 
            WHERE match_date < %s
            """
                
                cursor.execute(delete_sql, (cutoff_date,))
                deleted_count = cursor.rowcount
                
                conn.commit()
                cursor.close()
                # conn.close() # 由上下文管理器处理
                
                logger.info(f"清理了 {deleted_count} 条旧比赛记录")
                return deleted_count
                
        except Exception as e:
            logger.error(f"清理旧比赛数据失败: {e}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.close()
            return 0
    
    # 用户管理方法
    def create_user(self, username: str, email: str, password_hash: str, user_type: str = 'free') -> bool:
        """创建新用户"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                insert_sql = """
            INSERT INTO users (username, email, password_hash, user_type)
            VALUES (%s, %s, %s, %s)
            """
                cursor.execute(insert_sql, (username, email, password_hash, user_type))
                
                logger.info(f"用户创建成功: {username}")
                return True
                
        except psycopg2.IntegrityError as e:
            logger.warning(f"用户创建失败，用户名或邮箱已存在: {username}, {email}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.rollback() # 确保事务回滚
            #     conn.close()
            return False
        except Exception as e:
            logger.error(f"创建用户失败: {e}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.rollback() # 确保事务回滚
            #     conn.close()
            return False
    
    def authenticate_user(self, username: str, password_hash: str) -> dict:
        """用户认证"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) # 使用 RealDictCursor
                
                select_sql = """
            SELECT id, username, email, user_type, membership_expires, 
                   daily_predictions_used, last_prediction_date, total_predictions
            FROM users 
            WHERE username = %s AND password_hash = %s AND is_active = TRUE
            """
                cursor.execute(select_sql, (username, password_hash))
                user_data = cursor.fetchone()
                
                if user_data:
                    # 更新最后登录时间
                    update_sql = "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s"
                    cursor.execute(update_sql, (user_data['id'],))
                    conn.commit()
                    
                    # 检查是否需要重置每日使用次数
                    today = datetime.now().date()
                    last_prediction_date = user_data['last_prediction_date']
                    
                    if last_prediction_date and last_prediction_date < today:
                        reset_sql = """
                    UPDATE users SET daily_predictions_used = 0, last_prediction_date = %s 
                    WHERE id = %s
                    """
                        cursor.execute(reset_sql, (today, user_data['id']))
                        conn.commit()
                        user_data['daily_predictions_used'] = 0 # 更新返回的数据
                    
                    logger.info(f"用户认证成功: {username}")
                    return user_data # 直接返回字典格式的用户数据
                else:
                    logger.warning(f"用户认证失败: 用户名或密码错误 - {username}")
                    return None
                    
        except Exception as e:
            logger.error(f"用户认证失败: {e}", exc_info=True)
            return None
    
    def get_user_by_username(self, username: str) -> dict:
        """根据用户名获取用户信息"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) # 使用 RealDictCursor
                
                select_sql = """
            SELECT id, username, email, user_type, membership_expires, 
                   daily_predictions_used, last_prediction_date, total_predictions
            FROM users 
            WHERE username = %s AND is_active = TRUE
            """
                cursor.execute(select_sql, (username,))
                user_data = cursor.fetchone()
                
                if user_data:
                    # 检查是否需要重置每日使用次数 (这里只在获取时更新数据，不提交)
                    today = datetime.now().date()
                    last_prediction_date = user_data['last_prediction_date']

                    if last_prediction_date and last_prediction_date < today:
                        # 更新 user_data 字典中的值，以便返回最新状态
                        user_data['daily_predictions_used'] = 0
                        # 注意：这里不直接提交到数据库，因为 get_user_by_username 应该是一个只读操作
                        # 重置逻辑已在 authenticate_user 中处理
                    
                    return user_data # 直接返回字典格式的用户数据
                return None
                
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}", exc_info=True)
            return None
    
    def increment_user_predictions(self, user_id: int) -> bool:
        """增加用户预测次数"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                today = datetime.now().date()
                update_sql = """
            UPDATE users SET 
                daily_predictions_used = daily_predictions_used + 1,
                total_predictions = total_predictions + 1,
                last_prediction_date = %s
            WHERE id = %s
            """
                cursor.execute(update_sql, (today, user_id))
                
                return True
                
        except Exception as e:
            logger.error(f"更新用户预测次数失败: {e}")
            # if conn: # 只有当 conn 已经被赋值才尝试关闭
            #     conn.rollback()
            #     conn.close()
            return False
    
    def can_user_predict(self, user_id: int, user_type: str, daily_used: int) -> bool:
        """
        检查用户是否可以进行预测。
          admin   → 永远允许（无次数限制）
          premium → 永远允许
          free    → 今日次数 < 3 时允许
        """
        if user_type in ('admin', 'premium'):
            return True
        return daily_used < 3

    # ── 积分系统 ─────────────────────────────────────────────────────────────

    def get_user_credits(self, user_id: int) -> int:
        """获取用户当前积分"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                # 尝试从 credits 字段获取，若字段不存在返回默认值
                try:
                    cursor.execute("SELECT credits FROM users WHERE id = %s", (user_id,))
                    row = cursor.fetchone()
                    return row[0] if row and row[0] is not None else 0
                except Exception:
                    return 0
        except Exception as e:
            logger.error(f"获取积分失败: {e}")
            return 0

    def deduct_credits(self, user_id: int, cost: int) -> bool:
        """
        扣除用户积分，余额不足返回 False。
        admin 角色跳过扣分，直接返回 True。
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                # 查询角色 & 当前积分
                cursor.execute(
                    "SELECT user_type, credits FROM users WHERE id = %s FOR UPDATE",
                    (user_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return False
                user_type, current = row[0], (row[1] or 0)
                # admin 无限制，直接放行
                if user_type == 'admin':
                    return True
                if current < cost:
                    return False
                cursor.execute(
                    "UPDATE users SET credits = credits - %s WHERE id = %s",
                    (cost, user_id)
                )
                return True
        except Exception as e:
            logger.error(f"扣除积分失败: {e}")
            return False

    def add_credits(self, user_id: int, amount: int) -> bool:
        """增加用户积分"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET credits = COALESCE(credits, 0) + %s WHERE id = %s",
                    (amount, user_id)
                )
                return True
        except Exception as e:
            logger.error(f"增加积分失败: {e}")
            return False

    def checkin(self, user_id: int, user_type: str) -> dict:
        """
        每日签到：普通用户 +6，会员 +30。
        同一天重复签到返回 {'success': False, 'msg': '今日已签到'}
        """
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                today = datetime.now().date()

                # 检查今日是否已签到
                cursor.execute(
                    "SELECT last_checkin_date FROM users WHERE id = %s",
                    (user_id,)
                )
                row = cursor.fetchone()
                last_checkin = row[0] if row else None

                if last_checkin and last_checkin == today:
                    return {'success': False, 'msg': '今日已签到，明日再来'}

                amount = 30 if user_type == 'premium' else 6
                cursor.execute(
                    """UPDATE users SET
                        credits = COALESCE(credits, 0) + %s,
                        last_checkin_date = %s
                       WHERE id = %s""",
                    (amount, today, user_id)
                )

                # 查询最新积分
                cursor.execute("SELECT credits FROM users WHERE id = %s", (user_id,))
                new_credits = cursor.fetchone()[0]

                return {'success': True, 'added': amount, 'credits': new_credits}

        except Exception as e:
            logger.error(f"签到失败: {e}")
            return {'success': False, 'msg': str(e)}

    def ensure_credits_columns(self):
        """确保 users 表有 credits 和 last_checkin_date 字段（幂等操作）"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS credits INTEGER DEFAULT 20,
                        ADD COLUMN IF NOT EXISTS last_checkin_date DATE;
                """)
                logger.info("积分字段检查/添加完成")
        except Exception as e:
            logger.error(f"添加积分字段失败: {e}")

    def save_historical_matches(self, matches_list: List[Dict[str, Any]]) -> int:
        """
        保存历史比赛数据（替代原先写入CSV的逻辑）
        """
        saved_count = 0
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                for match in matches_list:
                    # 转换数据以适应表结构
                    match_id = str(match.get('match_id', ''))
                    home_team = match.get('home_team', '')
                    away_team = match.get('away_team', '')
                    league_name = match.get('league_name') or match.get('competition', '')
                    match_date_val = match.get('match_date')
                    
                    # 处理时间
                    match_date = None
                    match_time = None
                    match_datetime = None
                    if match_date_val:
                        try:
                            # 假设传入的是 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
                            if isinstance(match_date_val, str):
                                dt = datetime.fromisoformat(match_date_val.replace('Z', '+00:00'))
                            else:
                                dt = match_date_val
                            match_datetime = dt
                            match_date = dt.date()
                            match_time = dt.time()
                        except:
                            pass
                            
                    full_time_home_goals = match.get('home_score')
                    full_time_away_goals = match.get('away_score')
                    full_time_result = match.get('result')
                    half_time_home_goals = match.get('half_time_home')
                    half_time_away_goals = match.get('half_time_away')

                    insert_sql = """
                    INSERT INTO historical_matches (
                        match_id, home_team, away_team, league_name, 
                        match_date, match_time, match_datetime,
                        full_time_home_goals, full_time_away_goals, full_time_result,
                        half_time_home_goals, half_time_away_goals
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (match_id) DO UPDATE SET
                        home_team = EXCLUDED.home_team,
                        away_team = EXCLUDED.away_team,
                        league_name = EXCLUDED.league_name,
                        match_date = EXCLUDED.match_date,
                        match_time = EXCLUDED.match_time,
                        match_datetime = EXCLUDED.match_datetime,
                        full_time_home_goals = EXCLUDED.full_time_home_goals,
                        full_time_away_goals = EXCLUDED.full_time_away_goals,
                        full_time_result = EXCLUDED.full_time_result,
                        half_time_home_goals = EXCLUDED.half_time_home_goals,
                        half_time_away_goals = EXCLUDED.half_time_away_goals,
                        updated_at = CURRENT_TIMESTAMP
                    """
                    cursor.execute(insert_sql, (
                        match_id, home_team, away_team, league_name,
                        match_date, match_time, match_datetime,
                        full_time_home_goals, full_time_away_goals, full_time_result,
                        half_time_home_goals, half_time_away_goals
                    ))
                    saved_count += 1
                cursor.close()
            logger.info(f"✅ 成功将 {saved_count} 场历史比赛写入数据库")
            return saved_count
        except Exception as e:
            logger.error(f"写入 historical_matches 失败: {e}", exc_info=True)
            return saved_count

    def save_match_odds(self, odds_list: List[Dict[str, Any]]) -> int:
        """
        保存比赛赔率数据（替代原先写入CSV的逻辑）
        """
        saved_count = 0
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                for odd in odds_list:
                    match_id = str(odd.get('match_id', ''))
                    home_team = odd.get('home_team', '')
                    away_team = odd.get('away_team', '')
                    bookmaker = odd.get('bookmaker', '')
                    market = odd.get('market', '')
                    outcome = odd.get('outcome', '')
                    price = float(odd.get('price', 0.0))
                    
                    dt_val = odd.get('commence_time')
                    match_date = None
                    if dt_val:
                        try:
                            if isinstance(dt_val, str):
                                dt = datetime.fromisoformat(dt_val.replace('Z', '+00:00'))
                            else:
                                dt = dt_val
                            match_date = dt.date()
                        except:
                            pass

                    # match_odds 需要根据 outcome 填入 home_odds / draw_odds / away_odds
                    home_odds = price if outcome == home_team else None
                    draw_odds = price if outcome.lower() == 'draw' else None
                    away_odds = price if outcome == away_team else None

                    # 由于 ON CONFLICT 需要 unique (home_team, away_team, match_date, bookmaker)，如果已有记录则更新对应的赔率
                    insert_sql = """
                    INSERT INTO match_odds (
                        match_id, home_team, away_team, match_date, bookmaker,
                        home_odds, draw_odds, away_odds
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (home_team, away_team, match_date, bookmaker) DO UPDATE SET
                        home_odds = COALESCE(EXCLUDED.home_odds, match_odds.home_odds),
                        draw_odds = COALESCE(EXCLUDED.draw_odds, match_odds.draw_odds),
                        away_odds = COALESCE(EXCLUDED.away_odds, match_odds.away_odds),
                        updated_at = CURRENT_TIMESTAMP
                    """
                    cursor.execute(insert_sql, (
                        match_id, home_team, away_team, match_date, bookmaker,
                        home_odds, draw_odds, away_odds
                    ))
                    saved_count += 1
                cursor.close()
            logger.info(f"✅ 成功将 {saved_count} 条赔率数据写入数据库")
            return saved_count
        except Exception as e:
            logger.error(f"写入 match_odds 失败: {e}", exc_info=True)
            return saved_count

    def get_training_data(self) -> dict:
        """
        从数据库拉取训练所需数据，返回 DataFrame 格式字典
        """
        import pandas as pd
        try:
            with self.get_db_connection() as conn:
                matches_df = pd.read_sql("SELECT * FROM historical_matches ORDER BY match_datetime DESC", conn)
                odds_df = pd.read_sql("SELECT * FROM match_odds ORDER BY match_date DESC", conn)
            logger.info(f"✅ 成功从数据库读取 {len(matches_df)} 场比赛，{len(odds_df)} 条赔率")
            return {
                "matches": matches_df,
                "odds": odds_df
            }
        except Exception as e:
            logger.error(f"读取训练数据失败: {e}", exc_info=True)
            return {"matches": None, "odds": None}


# 创建全局数据库实例
prediction_db = PredictionDatabase()


def main():
    """测试函数"""
    try:
        db = PredictionDatabase()
        print("✅ 数据库连接成功")
        
        # 获取统计信息
        stats = db.get_prediction_stats()
        print(f"📊 统计信息: {stats}")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")

if __name__ == "__main__":
    main()
