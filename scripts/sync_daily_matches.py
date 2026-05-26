#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚠️ [已废弃] 每日比赛数据同步脚本
已被 sync_upcoming.py + sync_results.py 取代。
保留仅供参考，不再被任何活跃代码调用。
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import argparse

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.database import prediction_db
from scripts.china_lottery_spider import ChinaLotterySpider

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sync_matches.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# 中文联赛名 → league_code（用于写入 upcoming_fixtures）
LEAGUE_CODE_MAP = {
    "英超": "PL", "西甲": "PD", "意甲": "SA", "德甲": "BL1", "法甲": "FL1",
    "英冠": "ELC", "德乙": "BL2", "西乙": "PD2", "意乙": "SA2", "法乙": "FL2",
    "荷甲": "DED", "葡超": "PPL", "苏超": "PPL",
    "欧冠": "CL", "欧联": "EL", "欧协联": "ECL",
    "解放者杯": "CLI", "南美杯": "CSA", "巴甲": "BSA", "巴乙": "BSB",
    "美职联": "MLS", "日职联": "JPL", "中超": "CSL",
    "世界杯": "WC", "欧洲杯": "EC", "亚洲杯": "ASC",
}


class MatchSyncManager:
    """比赛数据同步管理器"""
    
    def __init__(self):
        self.spider = ChinaLotterySpider()
        self.db = prediction_db
    
    def sync_matches(self, days_ahead: int = 7, force_update: bool = False) -> Dict[str, int]:
        """
        同步比赛数据
        
        Args:
            days_ahead: 同步未来天数
            force_update: 是否强制更新
            
        Returns:
            同步统计信息
        """
        logger.info(f"🔄 开始同步未来 {days_ahead} 天的比赛数据...")
        
        try:
            # 从API获取最新数据
            logger.info("📡 正在从体彩官网获取数据...")
            matches_data = self.spider.get_formatted_matches(days_ahead=days_ahead)
            
            if not matches_data:
                logger.warning("⚠️ 未获取到任何比赛数据")
                return {'inserted': 0, 'updated': 0, 'skipped': 0}
            
            logger.info(f"✅ 成功获取 {len(matches_data)} 场比赛数据")
            
            # 保存到数据库
            logger.info("💾 正在保存到数据库...")
            stats = self.db.save_daily_matches(matches_data)
            
            logger.info(f"📊 同步完成 - 新增: {stats['inserted']}, 更新: {stats['updated']}, 跳过: {stats['skipped']}")
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ 同步失败: {e}")
            return {'inserted': 0, 'updated': 0, 'skipped': 0, 'error': str(e)}
    
    def cleanup_old_data(self, days_to_keep: int = 30) -> int:
        """
        清理旧数据
        
        Args:
            days_to_keep: 保留天数
            
        Returns:
            删除的记录数
        """
        logger.info(f"🧹 开始清理 {days_to_keep} 天前的旧数据...")
        
        try:
            deleted_count = self.db.cleanup_old_matches(days_to_keep)
            logger.info(f"✅ 清理完成，删除了 {deleted_count} 条记录")
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ 清理失败: {e}")
            return 0
    
    def get_database_stats(self) -> Dict[str, int]:
        """
        获取数据库统计信息
        
        Returns:
            统计信息字典
        """
        try:
            matches = self.db.get_daily_matches(days_ahead=30)
            
            # 按日期统计
            date_stats = {}
            league_stats = {}
            
            for match in matches:
                match_date = match.get('match_date', '')
                league = match.get('league_name', '未知')
                
                date_stats[match_date] = date_stats.get(match_date, 0) + 1
                league_stats[league] = league_stats.get(league, 0) + 1
            
            return {
                'total_matches': len(matches),
                'dates_count': len(date_stats),
                'leagues_count': len(league_stats),
                'date_stats': date_stats,
                'league_stats': league_stats
            }
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}
    
    def merge_to_upcoming_fixtures(self, days_ahead: int = 7) -> int:
        """
        把 daily_matches 里未来 N 天的比赛写入 upcoming_fixtures，
        让 AI 赛事广场显示彩票来源的比赛（如法甲附加赛、欧协联等）。
        fixture_id 以 'dm_' 前缀区分来源，避免与 football-data.org 数据冲突。
        """
        from datetime import datetime, timedelta, timezone
        merged = 0
        try:
            with self.db.get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT match_id, home_team, away_team, league_name,
                           match_datetime, home_odds, draw_odds, away_odds
                    FROM daily_matches
                    WHERE is_active = TRUE
                      AND match_datetime > NOW()
                      AND match_datetime < NOW() + INTERVAL '%s days'
                    ORDER BY match_datetime
                """ % int(days_ahead))
                rows = cur.fetchall()

                for match_id, home, away, league_name, match_dt, ho, do, ao in rows:
                    fixture_id = f"dm_{match_id}"
                    league_code = LEAGUE_CODE_MAP.get(league_name, "OTH")

                    # 如果同联赛同时段（±30分钟）已有 API 来源的比赛，跳过（避免重复）
                    cur.execute("""
                        SELECT 1 FROM upcoming_fixtures
                        WHERE league_code = %s
                          AND fixture_id NOT LIKE 'dm_%%'
                          AND ABS(EXTRACT(EPOCH FROM (match_time - %s))) < 1800
                        LIMIT 1
                    """, (league_code, match_dt))
                    if cur.fetchone():
                        continue  # API 已有该场比赛，跳过

                    cur.execute("""
                        INSERT INTO upcoming_fixtures
                            (fixture_id, league_code, league_name,
                             home_team, away_team, match_time, status,
                             home_odds, draw_odds, away_odds, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'SCHEDULED', %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (fixture_id) DO UPDATE SET
                            home_odds   = COALESCE(EXCLUDED.home_odds,   upcoming_fixtures.home_odds),
                            draw_odds   = COALESCE(EXCLUDED.draw_odds,   upcoming_fixtures.draw_odds),
                            away_odds   = COALESCE(EXCLUDED.away_odds,   upcoming_fixtures.away_odds),
                            updated_at  = CURRENT_TIMESTAMP
                    """, (fixture_id, league_code, league_name,
                          home, away, match_dt, ho, do, ao))
                    if cur.rowcount > 0:
                        merged += 1

                conn.commit()
        except Exception as e:
            logger.error(f"❌ 合并到 upcoming_fixtures 失败: {e}", exc_info=True)
        return merged

    def test_connection(self) -> bool:
        """
        测试数据库连接
        
        Returns:
            连接是否成功
        """
        try:
            conn = self.db.connect_to_database()
            conn.close()
            logger.info("✅ 数据库连接测试成功")
            return True
        except Exception as e:
            logger.error(f"❌ 数据库连接测试失败: {e}")
            return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='每日比赛数据同步脚本')
    parser.add_argument('--days', type=int, default=7, help='同步未来天数 (默认: 7)')
    parser.add_argument('--cleanup', type=int, help='清理多少天前的旧数据')
    parser.add_argument('--stats', action='store_true', help='显示数据库统计信息')
    parser.add_argument('--test', action='store_true', help='测试数据库连接')
    parser.add_argument('--force', action='store_true', help='强制更新所有数据')
    
    args = parser.parse_args()
    
    # 创建同步管理器
    sync_manager = MatchSyncManager()
    
    print("=" * 60)
    print("🏈 每日比赛数据同步脚本")
    print("=" * 60)
    
    # 测试连接
    if args.test:
        print("🔍 测试数据库连接...")
        if sync_manager.test_connection():
            print("✅ 数据库连接正常")
        else:
            print("❌ 数据库连接失败")
            return 1
    
    # 显示统计信息
    if args.stats:
        print("📊 获取数据库统计信息...")
        stats = sync_manager.get_database_stats()
        if stats:
            print(f"📈 总比赛数: {stats.get('total_matches', 0)}")
            print(f"📅 覆盖日期: {stats.get('dates_count', 0)} 天")
            print(f"🏆 联赛数量: {stats.get('leagues_count', 0)}")
            
            print("\n📅 按日期分布:")
            for date, count in sorted(stats.get('date_stats', {}).items()):
                print(f"  {date}: {count} 场")
            
            print("\n🏆 按联赛分布:")
            for league, count in sorted(stats.get('league_stats', {}).items(), key=lambda x: x[1], reverse=True):
                print(f"  {league}: {count} 场")
        else:
            print("❌ 无法获取统计信息")
    
    # 清理旧数据
    if args.cleanup:
        print(f"🧹 清理 {args.cleanup} 天前的旧数据...")
        deleted = sync_manager.cleanup_old_data(args.cleanup)
        print(f"✅ 清理完成，删除了 {deleted} 条记录")
    
    # 同步数据
    if not args.test and not args.stats and not args.cleanup:
        print(f"🔄 开始同步未来 {args.days} 天的比赛数据...")
        stats = sync_manager.sync_matches(days_ahead=args.days, force_update=args.force)
        
        if 'error' in stats:
            print(f"❌ 同步失败: {stats['error']}")
            return 1
        else:
            print("✅ 同步完成!")
            print(f"  📥 新增: {stats['inserted']} 场")
            print(f"  🔄 更新: {stats['updated']} 场")
            print(f"  ⏭️ 跳过: {stats['skipped']} 场")

        # 将彩票比赛合并到 upcoming_fixtures（赛事广场数据源）
        merged = sync_manager.merge_to_upcoming_fixtures(days_ahead=args.days)
        if merged:
            print(f"  🔀 已合并 {merged} 场到赛事广场")
    
    print("=" * 60)
    print("🎉 脚本执行完成")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断执行")
        sys.exit(1)
    except Exception as e:
        logger.error(f"脚本执行失败: {e}")
        sys.exit(1)
