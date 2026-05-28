#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导入历史赔率数据到 match_odds 表
数据源：football-data.co.uk 提供的免费 CSV（含 Bet365/Pinnacle 赔率）

用法:
  python -m matchpredict.tools.import_historical_odds
"""
import logging
import requests
import io
import pandas as pd

from matchpredict.utils.bootstrap import init_cli

init_cli()
logger = logging.getLogger(__name__)

# football-data.co.uk CSV URLs for major leagues (2023-2024 and 2024-2025 seasons)
ODDS_URLS = {
    # 2024-2025 season
    'E0_2425': 'https://www.football-data.co.uk/mmz4281/2425/E0.csv',   # EPL
    'SP1_2425': 'https://www.football-data.co.uk/mmz4281/2425/SP1.csv',  # La Liga
    'I1_2425': 'https://www.football-data.co.uk/mmz4281/2425/I1.csv',   # Serie A
    'D1_2425': 'https://www.football-data.co.uk/mmz4281/2425/D1.csv',   # Bundesliga
    'F1_2425': 'https://www.football-data.co.uk/mmz4281/2425/F1.csv',   # Ligue 1
    # 2023-2024 season
    'E0_2324': 'https://www.football-data.co.uk/mmz4281/2324/E0.csv',
    'SP1_2324': 'https://www.football-data.co.uk/mmz4281/2324/SP1.csv',
    'I1_2324': 'https://www.football-data.co.uk/mmz4281/2324/I1.csv',
    'D1_2324': 'https://www.football-data.co.uk/mmz4281/2324/D1.csv',
    'F1_2324': 'https://www.football-data.co.uk/mmz4281/2324/F1.csv',
}


def download_odds_csv(url: str) -> pd.DataFrame:
    """下载并解析赔率 CSV"""
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"  下载失败: {url} → HTTP {resp.status_code}")
            return pd.DataFrame()
        # 尝试不同编码
        for enc in ['utf-8', 'latin-1', 'cp1252']:
            try:
                df = pd.read_csv(io.StringIO(resp.content.decode(enc)), on_bad_lines='skip')
                return df
            except Exception:
                continue
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"  下载异常: {e}")
        return pd.DataFrame()


def import_odds_to_db(db):
    """下载所有历史赔率并写入 match_odds 表"""
    total_imported = 0

    for key, url in ODDS_URLS.items():
        logger.info(f"📥 下载 {key}...")
        df = download_odds_csv(url)
        if df.empty:
            continue

        # football-data.co.uk CSV 列名：
        # Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, B365H, B365D, B365A, ...
        required_cols = ['Date', 'HomeTeam', 'AwayTeam']
        if not all(c in df.columns for c in required_cols):
            logger.warning(f"  {key} 缺少必要列，跳过")
            continue

        # 选择赔率列（优先 Pinnacle，其次 Bet365）
        if 'PSH' in df.columns and 'PSD' in df.columns and 'PSA' in df.columns:
            odds_cols = ('PSH', 'PSD', 'PSA')
            bookmaker = 'Pinnacle'
        elif 'B365H' in df.columns and 'B365D' in df.columns and 'B365A' in df.columns:
            odds_cols = ('B365H', 'B365D', 'B365A')
            bookmaker = 'Bet365'
        else:
            logger.warning(f"  {key} 无可用赔率列，跳过")
            continue

        imported = 0
        try:
            with db.get_db_connection() as conn:
                cur = conn.cursor()
                for _, row in df.iterrows():
                    try:
                        home = str(row['HomeTeam']).strip()
                        away = str(row['AwayTeam']).strip()
                        date_str = str(row['Date']).strip()
                        ho = row[odds_cols[0]]
                        do = row[odds_cols[1]]
                        ao = row[odds_cols[2]]

                        if pd.isna(ho) or pd.isna(do) or pd.isna(ao):
                            continue
                        if not home or not away:
                            continue

                        # 解析日期（格式可能是 DD/MM/YYYY 或 YYYY-MM-DD）
                        match_date = None
                        for fmt in ['%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d']:
                            try:
                                match_date = pd.to_datetime(date_str, format=fmt).date()
                                break
                            except Exception:
                                continue
                        if not match_date:
                            continue

                        cur.execute("""
                            INSERT INTO match_odds (home_team, away_team, match_date, bookmaker,
                                home_odds, draw_odds, away_odds,
                                open_home_odds, open_draw_odds, open_away_odds, odds_source)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (home_team, away_team, match_date, bookmaker) DO UPDATE SET
                                home_odds = EXCLUDED.home_odds,
                                draw_odds = EXCLUDED.draw_odds,
                                away_odds = EXCLUDED.away_odds,
                                updated_at = CURRENT_TIMESTAMP
                        """, (home, away, match_date, bookmaker,
                              float(ho), float(do), float(ao),
                              float(ho), float(do), float(ao),
                              'football-data.co.uk'))
                        imported += 1
                    except Exception:
                        continue
                conn.commit()
        except Exception as e:
            logger.error(f"  写入失败: {e}")

        logger.info(f"  ✅ {key}: 导入 {imported} 条赔率")
        total_imported += imported

    return total_imported


def main():
    from matchpredict.db import prediction_db as db

    print("=" * 60)
    print("💰 导入历史赔率数据（football-data.co.uk）")
    print("=" * 60)

    total = import_odds_to_db(db)
    print(f"\n✅ 共导入 {total} 条历史赔率")
    print("   现在可以重新训练模型: make train")


if __name__ == "__main__":
    main()
