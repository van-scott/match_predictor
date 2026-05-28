#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日赛事同步入口（已并入 sync_upcoming）。

说明：
1. 当前项目只保留一条“赛事+赔率+ML”的同步链路：scripts/sync_upcoming.py。
2. 本脚本作为兼容入口，避免旧定时任务/文档调用失败。
3. 默认同步窗口由 sync_upcoming.py 的 SYNC_WINDOW_DAYS 控制（默认 7 天）。
"""

import argparse
import os
import subprocess
import sys


def main():
    """转发到统一同步脚本。"""
    parser = argparse.ArgumentParser(description='每日赛事同步（兼容入口）')
    parser.add_argument('--days', type=int, default=None, help='同步未来天数；未传则用全局默认 SYNC_WINDOW_DAYS')
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, os.path.join(root, "scripts", "sync_upcoming.py")]
    if args.days is not None:
        cmd += ["--days", str(args.days)]
    print("ℹ️ sync_daily_matches 已并入 sync_upcoming，开始执行统一同步链路...")
    completed = subprocess.run(cmd)
    return completed.returncode


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
