import os
import sys
from datetime import datetime, timedelta

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.database import prediction_db

def mock_init_data():
    print("🚀 开始填充模拟数据...")
    
    # 1. 模拟历史比赛 (historical_matches)
    matches = [
        {
            'match_id': 'hist_001',
            'home_team': '阿森纳',
            'away_team': '曼城',
            'competition': '英超',
            'match_date': (datetime.now() - timedelta(days=5)).isoformat(),
            'home_score': 2,
            'away_score': 1,
            'result': 'H'
        },
        {
            'match_id': 'hist_002',
            'home_team': '皇马',
            'away_team': '巴萨',
            'competition': '西甲',
            'match_date': (datetime.now() - timedelta(days=3)).isoformat(),
            'home_score': 3,
            'away_score': 2,
            'result': 'H'
        }
    ]
    prediction_db.save_historical_matches(matches)
    
    # 2. 模拟赔率 (match_odds)
    odds = [
        {
            'match_id': 'fixture_001',
            'home_team': '利物浦',
            'away_team': '切尔西',
            'bookmaker': 'B365',
            'commence_time': (datetime.now() + timedelta(days=1)).isoformat(),
            'market': 'h2h',
            'outcome': '利物浦',
            'price': 1.85
        },
        {
            'match_id': 'fixture_001',
            'home_team': '利物浦',
            'away_team': '切尔西',
            'bookmaker': 'B365',
            'commence_time': (datetime.now() + timedelta(days=1)).isoformat(),
            'market': 'h2h',
            'outcome': 'Draw',
            'price': 3.60
        }
    ]
    prediction_db.save_match_odds(odds)
    
    # 3. 模拟球队战力 (team_ratings) - 直接通过 SQL
    with prediction_db.get_db_connection() as conn:
        cursor = conn.cursor()
        ratings = [
            ('曼城', '英超', 2050.5, 1.8),
            ('皇马', '西甲', 2010.2, 1.5),
            ('拜仁', '德甲', 1980.0, 1.4)
        ]
        for r in ratings:
            cursor.execute("""
                INSERT INTO team_ratings (team_name, league_name, elo_rating, pi_rating)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (team_name) DO UPDATE SET
                    elo_rating = EXCLUDED.elo_rating,
                    pi_rating = EXCLUDED.pi_rating,
                    updated_at = CURRENT_TIMESTAMP
            """, r)
        conn.commit()
        print(f"✅ 成功插入 {len(ratings)} 条球队评分数据")

    print("🎉 模拟数据填充完成！你可以去查看数据库了。")

if __name__ == "__main__":
    mock_init_data()
