#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国体彩 JSON API 客户端
─────────────────────
仅用于拉取 hhad (让一球/胜平负) 赔率，供数据流水线 Step 1 合并到 upcoming_fixtures。
跟 `lottery_api.ChinaSportsLotterySpider`（HTML 爬虫）共用同一站点，
但走的是结构化 JSON 接口，更适合做赔率同步。
"""

import requests
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import time
import random

logger = logging.getLogger(__name__)

class ChinaLotterySpider:
    """中国体育彩票爬虫"""
    
    def __init__(self):
        self.base_url = "https://webapi.sporttery.cn"
        self.api_endpoint = "/gateway/uniform/football/getMatchCalculatorV1.qry"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://www.lottery.gov.cn/",
            "Origin": "https://www.lottery.gov.cn"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def fetch_lottery_data(self, pool_code: str = "hhad", channel: str = "c") -> Optional[Dict[str, Any]]:
        """
        获取彩票数据
        
        Args:
            pool_code: 池子代码 (hhad=让球胜平负, had=胜平负, spf=胜平负)
            channel: 渠道 (c=通用, pc=PC, wap=移动端)
        
        Returns:
            API响应数据或None
        """
        url = f"{self.base_url}{self.api_endpoint}"
        params = {
            "poolCode": pool_code,
            "channel": channel
        }
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"正在获取体彩数据 (尝试 {attempt + 1}/{max_retries}): {url}")
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                
                data = response.json()
                
                if data.get('success'):
                    logger.info(f"✅ 成功获取API数据: {data.get('errorMessage', '处理成功')}")
                    return data
                else:
                    error_msg = data.get('errorMessage', '未知错误')
                    logger.warning(f"⚠️ API返回错误: {error_msg}")
                    if attempt == max_retries - 1:
                        raise Exception(f"API调用失败: {error_msg}")
                    
            except requests.exceptions.RequestException as e:
                logger.warning(f"❌ 网络请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    wait_time = random.uniform(1, 3)
                    logger.info(f"⏳ 等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                else:
                    raise Exception(f"网络请求失败: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON解析失败: {e}")
                raise Exception(f"响应数据格式错误: {e}")
            except Exception as e:
                logger.error(f"❌ 获取数据失败: {e}")
                if attempt == max_retries - 1:
                    raise
                
        return None

    def parse_match_data(self, api_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析API数据为标准格式
        
        Args:
            api_data: API返回的原始数据
            
        Returns:
            标准化的比赛数据列表
        """
        matches = []
        
        try:
            # 获取比赛列表
            value = api_data.get('value', {})
            match_info_list = value.get('matchInfoList', [])
            
            if not match_info_list:
                logger.warning("⚠️ API返回的数据中没有比赛信息")
                return matches
            
            logger.info(f"📊 开始解析 {len(match_info_list)} 个日期的比赛")
            
            # 遍历每个日期的比赛
            for date_info in match_info_list:
                sub_match_list = date_info.get('subMatchList', [])
                business_date = date_info.get('businessDate', '')
                
                logger.info(f"📅 处理 {business_date} 的 {len(sub_match_list)} 场比赛")
                
                for match_data in sub_match_list:
                    try:
                        # 提取基本信息
                        match_info = {
                            'match_id': f"lottery_{match_data.get('matchId', '')}",
                            'home_team': self.clean_team_name(match_data.get('homeTeamAllName', match_data.get('homeTeamAbbName', ''))),
                            'away_team': self.clean_team_name(match_data.get('awayTeamAllName', match_data.get('awayTeamAbbName', ''))),
                            'league_name': match_data.get('leagueAbbName', match_data.get('leagueAllName', '')),
                            'match_time': f"{match_data.get('matchDate', '')} {match_data.get('matchTime', '')}",
                            'match_date': match_data.get('matchDate', ''),
                            'match_num': match_data.get('matchNumStr', ''),
                            'status': match_data.get('matchStatus', 'Unknown'),
                            'source': 'china_lottery'
                        }
                        
                        # 提取赔率信息
                        odds_info = self.extract_odds(match_data)
                        if odds_info:
                            match_info['odds'] = odds_info
                            
                            # 验证数据完整性
                            if self.validate_match(match_info):
                                matches.append(match_info)
                                logger.debug(f"✅ 成功解析比赛: {match_info['home_team']} vs {match_info['away_team']}")
                            else:
                                logger.warning(f"⚠️ 比赛数据不完整，跳过: {match_info}")
                        else:
                            logger.warning(f"⚠️ 无法获取赔率信息，跳过比赛: {match_data.get('homeTeamAbbName', '')} vs {match_data.get('awayTeamAbbName', '')}")
                            
                    except Exception as e:
                        logger.warning(f"⚠️ 解析单场比赛失败: {e}")
                        continue
            
            logger.info(f"📈 成功解析 {len(matches)} 场有效比赛")
            return matches
            
        except Exception as e:
            logger.error(f"❌ 解析比赛数据失败: {e}")
            raise Exception(f"数据解析错误: {e}")

    def extract_odds(self, match_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        提取比赛赔率信息（提取数据但使用hhad键名保持兼容性）
        
        Args:
            match_data: 单场比赛数据
            
        Returns:
            赔率信息字典（统一使用hhad键名）
        """
        try:
            # 方法1: 从当前数据的had字段提取 (不让球胜平负)
            if 'had' in match_data and match_data['had']:
                had_data = match_data['had']
                if all(key in had_data for key in ['h', 'd', 'a']):
                    return {
                        'hhad': {  # 使用hhad键名保持兼容性
                            'h': str(had_data['h']),
                            'd': str(had_data['d']),
                            'a': str(had_data['a'])
                        },
                        'type': 'had',  # 但标记实际类型为不让球胜平负
                        'update_time': f"{had_data.get('updateDate', '')} {had_data.get('updateTime', '')}"
                    }
            
            # 方法2: 从oddsList中查找HAD类型
            if 'oddsList' in match_data and match_data['oddsList']:
                for odds_item in match_data['oddsList']:
                    if odds_item.get('poolCode') == 'HAD' and all(key in odds_item for key in ['h', 'd', 'a']):
                        return {
                            'hhad': {  # 使用hhad键名保持兼容性
                                'h': str(odds_item['h']),
                                'd': str(odds_item['d']),
                                'a': str(odds_item['a'])
                            },
                            'type': 'had',  # 但标记实际类型为不让球胜平负
                            'update_time': f"{odds_item.get('updateDate', '')} {odds_item.get('updateTime', '')}"
                        }
            
            # 方法3: 备用 - 从hhad字段提取 (让球胜平负)
            if 'hhad' in match_data and match_data['hhad']:
                hhad_data = match_data['hhad']
                if all(key in hhad_data for key in ['h', 'd', 'a']):
                    return {
                        'hhad': {
                            'h': str(hhad_data['h']),
                            'd': str(hhad_data['d']),
                            'a': str(hhad_data['a'])
                        },
                        'type': 'hhad',  # 标记类型为让球胜平负
                        'goal_line': hhad_data.get('goalLine', ''),
                        'update_time': f"{hhad_data.get('updateDate', '')} {hhad_data.get('updateTime', '')}"
                    }
            
            # 方法4: 从oddsList中查找HHAD类型作为最后备用
            if 'oddsList' in match_data and match_data['oddsList']:
                for odds_item in match_data['oddsList']:
                    if odds_item.get('poolCode') == 'HHAD' and all(key in odds_item for key in ['h', 'd', 'a']):
                        return {
                            'hhad': {
                                'h': str(odds_item['h']),
                                'd': str(odds_item['d']),
                                'a': str(odds_item['a'])
                            },
                            'type': 'hhad',  # 标记类型为让球胜平负
                            'goal_line': odds_item.get('goalLine', ''),
                            'update_time': f"{odds_item.get('updateDate', '')} {odds_item.get('updateTime', '')}"
                        }
            
            logger.debug(f"⚠️ 未找到有效赔率数据: {match_data.get('homeTeamAbbName', '')} vs {match_data.get('awayTeamAbbName', '')}")
            return None
            
        except Exception as e:
            logger.warning(f"⚠️ 提取赔率失败: {e}")
            return None

    def clean_team_name(self, team_name: str) -> str:
        """
        清理球队名称
        
        Args:
            team_name: 原始球队名称
            
        Returns:
            清理后的球队名称
        """
        if not team_name:
            return ""
        
        # 移除常见的后缀和前缀
        cleaned = team_name.strip()
        
        # 移除括号内容和排名信息
        import re
        cleaned = re.sub(r'\[.*?\]', '', cleaned)  # 移除 [排名信息]
        cleaned = re.sub(r'\(.*?\)', '', cleaned)  # 移除 (其他信息)
        
        return cleaned.strip()

    def validate_match(self, match: Dict[str, Any]) -> bool:
        """
        验证比赛数据的完整性
        
        Args:
            match: 比赛数据
            
        Returns:
            是否有效
        """
        required_fields = ['match_id', 'home_team', 'away_team', 'league_name', 'odds']
        
        # 检查必需字段
        for field in required_fields:
            if not match.get(field):
                logger.debug(f"❌ 缺少必需字段: {field}")
                return False
        
        # 检查赔率数据
        odds = match.get('odds', {})
        if 'hhad' not in odds:
            logger.debug("❌ 缺少赔率数据")
            return False
        
        hhad = odds['hhad']
        if not all(key in hhad and hhad[key] for key in ['h', 'd', 'a']):
            logger.debug("❌ 赔率数据不完整")
            return False
        
        # 验证赔率数值
        try:
            for odds_value in [hhad['h'], hhad['d'], hhad['a']]:
                float_val = float(odds_value)
                if float_val < 1.01 or float_val > 99.99:
                    logger.debug(f"❌ 赔率数值异常: {odds_value}")
                    return False
        except (ValueError, TypeError):
            logger.debug("❌ 赔率数值格式错误")
            return False
        
        return True

    def filter_matches_by_date(self, matches: List[Dict[str, Any]], days_ahead: int = 3) -> List[Dict[str, Any]]:
        """
        根据日期过滤比赛
        
        Args:
            matches: 比赛列表
            days_ahead: 未来天数
            
        Returns:
            过滤后的比赛列表
        """
        if not matches:
            return []
        
        try:
            current_date = datetime.now().date()
            end_date = current_date + timedelta(days=days_ahead)
            
            filtered_matches = []
            for match in matches:
                match_date_str = match.get('match_date', '')
                if match_date_str:
                    try:
                        match_date = datetime.strptime(match_date_str, '%Y-%m-%d').date()
                        if current_date <= match_date <= end_date:
                            filtered_matches.append(match)
                    except ValueError:
                        logger.warning(f"⚠️ 日期格式错误: {match_date_str}")
                        continue
            
            logger.info(f"📅 日期过滤: {len(matches)} -> {len(filtered_matches)} 场比赛 (未来{days_ahead}天)")
            return filtered_matches
            
        except Exception as e:
            logger.error(f"❌ 过滤比赛失败: {e}")
            return matches

    def get_formatted_matches(self, days_ahead: int = 3) -> List[Dict[str, Any]]:
        """
        获取格式化的比赛数据（优先获取不让球胜平负）
        
        Args:
            days_ahead: 未来天数
            
        Returns:
            格式化的比赛数据列表
        """
        try:
            # 先尝试获取HAD（不让球胜平负）数据
            logger.info("🎯 尝试获取不让球胜平负(HAD)数据...")
            had_data = None
            try:
                had_data = self.fetch_lottery_data(pool_code="had")
                if had_data:
                    logger.info("✅ 成功获取HAD数据")
            except Exception as e:
                logger.warning(f"⚠️ 获取HAD数据失败: {e}")
            
            # 再获取HHAD（让球胜平负）数据作为备用
            logger.info("🎯 获取让球胜平负(HHAD)数据作为补充...")
            hhad_data = self.fetch_lottery_data(pool_code="hhad")
            if not hhad_data:
                raise Exception("无法获取任何API数据")
            
            # 解析比赛数据 - 先用HAD数据，再用HHAD补充
            matches = self.parse_match_data_with_odds_priority(had_data, hhad_data)
            if not matches:
                raise Exception("未能解析到有效的比赛数据")
            
            # 按日期过滤
            filtered_matches = self.filter_matches_by_date(matches, days_ahead)
            if not filtered_matches:
                raise Exception(f"未来{days_ahead}天内没有可用的比赛")
            
            # 统计赔率类型
            had_count = sum(1 for m in filtered_matches if m.get('odds', {}).get('type') == 'had')
            hhad_count = sum(1 for m in filtered_matches if m.get('odds', {}).get('type') == 'hhad')
            logger.info(f"📊 赔率统计: 不让球{had_count}场, 让球{hhad_count}场")
            logger.info(f"✅ 成功获取 {len(filtered_matches)} 场比赛数据")
            return filtered_matches
            
        except Exception as e:
            logger.error(f"❌ 获取格式化比赛数据失败: {e}")
            raise Exception(f"暂时无法获取体彩数据: {e}")
    
    def parse_match_data_with_odds_priority(self, had_data: Optional[Dict[str, Any]], hhad_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析比赛数据，优先使用HAD赔率，HHAD作为补充
        
        Args:
            had_data: HAD数据（可能为None）
            hhad_data: HHAD数据
            
        Returns:
            标准化的比赛数据列表
        """
        matches = []
        
        # 创建HAD数据的映射表（按matchId索引）
        had_odds_map = {}
        if had_data and 'value' in had_data:
            for date_info in had_data['value'].get('matchInfoList', []):
                for match_data in date_info.get('subMatchList', []):
                    match_id = match_data.get('matchId', '')
                    if match_id and 'had' in match_data and match_data['had']:
                        had_odds = match_data['had']
                        if all(key in had_odds for key in ['h', 'd', 'a']):
                            had_odds_map[match_id] = {
                                'hhad': {  # 使用hhad键名保持兼容性
                                    'h': str(had_odds['h']),
                                    'd': str(had_odds['d']),
                                    'a': str(had_odds['a'])
                                },
                                'type': 'had',  # 但标记类型为不让球胜平负
                                'update_time': f"{had_odds.get('updateDate', '')} {had_odds.get('updateTime', '')}"
                            }
        # 创建 HHAD（让球盘）映射，和 HAD 并行保留，避免“优先 HAD 后丢失 HHAD”。
        hhad_odds_map = {}
        if hhad_data and 'value' in hhad_data:
            for date_info in hhad_data['value'].get('matchInfoList', []):
                for match_data in date_info.get('subMatchList', []):
                    match_id = match_data.get('matchId', '')
                    if not match_id:
                        continue
                    hhad_raw = match_data.get('hhad')
                    if not hhad_raw:
                        continue
                    if all(key in hhad_raw for key in ['h', 'd', 'a']):
                        hhad_odds_map[match_id] = {
                            'h': str(hhad_raw.get('h')),
                            'd': str(hhad_raw.get('d')),
                            'a': str(hhad_raw.get('a')),
                            'goal_line': hhad_raw.get('goalLine') or hhad_raw.get('goal_line') or '',
                            'update_time': f"{hhad_raw.get('updateDate', '')} {hhad_raw.get('updateTime', '')}",
                        }
        
        # 处理HHAD数据，并优先使用HAD赔率
        try:
            value = hhad_data.get('value', {})
            match_info_list = value.get('matchInfoList', [])
            
            logger.info(f"📊 开始解析比赛数据 (HAD映射: {len(had_odds_map)} 场)")
            
            for date_info in match_info_list:
                sub_match_list = date_info.get('subMatchList', [])
                business_date = date_info.get('businessDate', '')
                
                logger.info(f"📅 处理 {business_date} 的 {len(sub_match_list)} 场比赛")
                
                for match_data in sub_match_list:
                    try:
                        match_id = match_data.get('matchId', '')
                        
                        # 提取基本信息
                        match_info = {
                            'match_id': f"lottery_{match_id}",
                            'home_team': self.clean_team_name(match_data.get('homeTeamAllName', match_data.get('homeTeamAbbName', ''))),
                            'away_team': self.clean_team_name(match_data.get('awayTeamAllName', match_data.get('awayTeamAbbName', ''))),
                            'league_name': match_data.get('leagueAbbName', match_data.get('leagueAllName', '')),
                            'match_time': f"{match_data.get('matchDate', '')} {match_data.get('matchTime', '')}",
                            'match_date': match_data.get('matchDate', ''),
                            'match_num': match_data.get('matchNumStr', ''),
                            'status': match_data.get('matchStatus', 'Unknown'),
                            'source': 'china_lottery'
                        }
                        
                        # 优先使用HAD赔率
                        odds_info = None
                        if match_id in had_odds_map:
                            odds_info = had_odds_map[match_id]
                            logger.debug(f"✅ 使用HAD赔率: {match_info['home_team']} vs {match_info['away_team']}")
                        else:
                            # 使用HHAD赔率作为备用
                            odds_info = self.extract_odds(match_data)
                            if odds_info and odds_info.get('type') == 'hhad':
                                logger.debug(f"⚠️ 使用HHAD赔率: {match_info['home_team']} vs {match_info['away_team']}")
                        
                        if odds_info:
                            match_info['odds'] = odds_info
                            # 即使本场主赔率用 HAD，也把 HHAD 侧写带上，供下游入库到 hhad_* 字段。
                            if match_id in hhad_odds_map:
                                match_info['hhad_odds'] = hhad_odds_map[match_id]
                            
                            # 验证数据完整性
                            if self.validate_match(match_info):
                                matches.append(match_info)
                            else:
                                logger.warning(f"⚠️ 比赛数据不完整，跳过: {match_info}")
                        else:
                            logger.warning(f"⚠️ 无法获取任何赔率信息，跳过比赛: {match_data.get('homeTeamAbbName', '')} vs {match_data.get('awayTeamAbbName', '')}")
                            
                    except Exception as e:
                        logger.warning(f"⚠️ 解析单场比赛失败: {e}")
                        continue
            
            logger.info(f"📈 成功解析 {len(matches)} 场有效比赛")
            return matches
            
        except Exception as e:
            logger.error(f"❌ 解析比赛数据失败: {e}")
            raise Exception(f"数据解析错误: {e}")


def main():
    """测试函数"""
    spider = ChinaLotterySpider()
    
    try:
        print("🕷️ 测试中国体育彩票爬虫...")
        matches = spider.get_formatted_matches(days_ahead=7)
        
        print(f"\n✅ 成功获取 {len(matches)} 场比赛")
        
        # 显示前3场比赛
        for i, match in enumerate(matches[:3]):
            print(f"\n比赛 {i+1}:")
            print(f"  {match['match_num']}: {match['home_team']} vs {match['away_team']}")
            print(f"  联赛: {match['league_name']}")
            print(f"  时间: {match['match_time']}")
            if 'hhad' in match['odds']:
                odds = match['odds']['hhad']
                odds_type = match['odds'].get('type', 'hhad')
                type_label = "不让球" if odds_type == 'had' else "让球"
                print(f"  赔率({type_label}): 主胜{odds['h']} 平局{odds['d']} 客胜{odds['a']}")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")


if __name__ == "__main__":
    main()