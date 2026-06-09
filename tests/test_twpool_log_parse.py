#!/usr/bin/env python3
"""parse_latest_hashrate 解析 twpool 镜像日志格式:
'HH:MM:SS | 134.6 TH/s window | 135.2 TH/s avg | shares: N accepted [| queue: x/64, dropped: y]'
取 window(当前)值; 无 window 退 avg; 保留 pearlhash 旧格式。
运行: python3 tests/test_twpool_log_parse.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n)
    fails += 0 if c else 1

f = S.parse_latest_hashrate
ck("twpool window 值 134.6",
   abs((f("03:59:41 | 134.6 TH/s window | 135.2 TH/s avg | shares: 143 accepted") or 0) - 134.6) < 0.01)
ck("twpool 带 queue 取 window 135.4",
   abs((f("03:58:21 | 135.4 TH/s window | 135.2 TH/s avg | shares: 141 accepted | queue: 1/64, dropped: 0") or 0) - 135.4) < 0.01)
multi = "\n".join(['"[new job]"',
                   "03:58:01 | 134.0 TH/s window | 135.2 TH/s avg | shares: 141 accepted",
                   '"[new job]"',
                   "03:59:41 | 136.7 TH/s window | 135.2 TH/s avg | shares: 143 accepted"])
ck("多行取最后一条 window 136.7", abs((f(multi) or 0) - 136.7) < 0.01)
ck("只有 [new job] → None", f('"[new job]"\n"[new job]"') is None)
ck("无 window 退 avg 138.0",
   abs((f("12:00:00 | 138.0 TH/s avg | shares: 5 accepted") or 0) - 138.0) < 0.01)
ck("pearlhash 旧格式回归 140.5",
   abs((f("blah Hashrate Total = 140.5 TH/s blah") or 0) - 140.5) < 0.01)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
