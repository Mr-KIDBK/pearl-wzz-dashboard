#!/usr/bin/env python3
"""salad_portal.should_relogin: 判定是否该触发 scid 重登(缺失 或 连续抓空≥阈值=过期, 且距上次尝试≥冷却)。
运行: python3 tests/test_scid_relogin.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import salad_portal as S
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# 缺失 + 冷却已过(last=0, now=10000) → 该重登
ck("缺失+冷却过 → 重登", S.should_relogin(True, 0, 0, 10000) is True)
# 连续 2 轮空(达阈值) + 冷却过 → 该重登(过期)
ck("连续2轮空 → 重登", S.should_relogin(False, 2, 0, 10000) is True)
# 连续 1 轮空(未达阈值) → 不重登(单轮抖动不算过期)
ck("连续1轮空 → 不重登", S.should_relogin(False, 1, 0, 10000) is False)
# 缺失但冷却内(刚 100s 前尝试过, cooldown=1800) → 不重登(防反复弹窗)
ck("冷却内 → 不重登", S.should_relogin(True, 5, 10000 - 100, 10000, cooldown=1800) is False)
# 健康(非缺失 + 0 空) → 不重登
ck("健康 → 不重登", S.should_relogin(False, 0, 0, 10000) is False)
# 边界: 连续 3 空(超阈值)+ 冷却过 → 重登
ck("连续3空 → 重登", S.should_relogin(False, 3, 0, 10000) is True)
# 冷却边界: 恰好满 1800s → 重登
ck("冷却恰好满 → 重登", S.should_relogin(True, 0, 0, 1800, cooldown=1800) is True)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
