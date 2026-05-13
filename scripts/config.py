import os

# 基础配置 (来自 config_example.py)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"
DEBUG = True
SECRET_KEY = "your_secret_key_here"

# 外部API配置
FOOTBALL_DATA_BASE_URL = "http://api.football-data.org/v4"
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "d318f21f939e4752a93313937fd203e9")

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "demo")

# 默认抓取参数
DEFAULT_LEAGUE = "PL"
DEFAULT_SEASON = 2024
DATA_DIR = "data"
MATCHES_DATA_FILE = "data/matches_data.csv"
ODDS_DATA_FILE = "data/odds_data.csv"
