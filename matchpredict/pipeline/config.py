# -*- coding: utf-8 -*-
"""
流水线全局配置
──────────────
所有联赛代码、同步窗口、赔率 API 映射都在这里集中管理。
调度器、各步骤函数、脚本入口统一 import 这里，避免多处维护。
"""
import os

# ── 同步窗口 ──────────────────────────────────────────────────────────────────
# 可通过环境变量覆盖，方便不同环境调节
SYNC_WINDOW_DAYS: int = int(os.getenv("SYNC_WINDOW_DAYS", "7"))
RESULT_WINDOW_DAYS: int = int(os.getenv("RESULT_WINDOW_DAYS", "7"))

# ── 联赛列表（赛程同步 & 结果回填共用，按优先级排序）────────────────────────
LEAGUES: list[str] = [
    "PL",   # 英超
    "PD",   # 西甲
    "SA",   # 意甲
    "BL1",  # 德甲
    "FL1",  # 法甲
    "CL",   # 欧冠
    "ELC",  # 英冠
    "DED",  # 荷甲
    "PPL",  # 葡超
    "BSA",  # 巴甲
    "CLI",  # 解放者杯
]

# ── 联赛显示名 ────────────────────────────────────────────────────────────────
LEAGUE_NAMES: dict[str, str] = {
    "PL":  "英超",
    "PD":  "西甲",
    "SA":  "意甲",
    "BL1": "德甲",
    "FL1": "法甲",
    "CL":  "欧冠",
    "EL":  "欧联",
    "ELC": "英冠",
    "DED": "荷甲",
    "PPL": "葡超",
    "BSA": "巴甲",
    "CLI": "解放者杯",
}

# ── football-data.org ─────────────────────────────────────────────────────────
FOOTBALL_DATA_API_KEY: str = os.getenv(
    "FOOTBALL_DATA_API_KEY", "d318f21f939e4752a93313937fd203e9"
)
FOOTBALL_DATA_BASE_URL: str = "https://api.football-data.org/v4"

# ── the-odds-api.com ──────────────────────────────────────────────────────────
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "bacff6d40e0464574885dcc2bdcb9833")
ODDS_API_BASE_URL: str = "https://api.the-odds-api.com/v4/sports"

# 联赛代码 → the-odds-api sport key
ODDS_SPORT_MAP: dict[str, str] = {
    "PL":  "soccer_epl",
    "PD":  "soccer_spain_la_liga",
    "SA":  "soccer_italy_serie_a",
    "BL1": "soccer_germany_bundesliga",
    "FL1": "soccer_france_ligue_one",
    "CL":  "soccer_uefa_champs_league",
    "BSA": "soccer_brazil_campeonato",
    "CLI": "soccer_conmebol_copa_libertadores",
    "ELC": "soccer_england_league1",
    "DED": "soccer_netherlands_eredivisie",
    "PPL": "soccer_portugal_primeira_liga",
}

# ── ML 模型 ───────────────────────────────────────────────────────────────────
MODEL_PATH: str = os.getenv(
    "MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "models", "match_predictor_all.pkl"),
)
