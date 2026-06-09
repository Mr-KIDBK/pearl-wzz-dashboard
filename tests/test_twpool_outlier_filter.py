#!/usr/bin/env python3
"""_twpool_view 剔除矿池上报的损坏算力(单 worker 物理上不可能的超大值),
防止总算力/算力性价比被污染。正常(含偏高如 260TH)保留。
运行: python3 tests/test_twpool_outlier_filter.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n)
    fails += 0 if c else 1

ADDR = "prl1test"
D.prl_address = lambda: ADDR
D.twpool_data = lambda force=False: {"reported": {
    f"{ADDR}.gpu16": {"hs": 139834438340522},        # 139.83 TH/s 正常
    f"{ADDR}.rp2":   {"hs": 260000000000000},         # 260 TH/s 正常(偏高但合理)
    f"{ADDR}.gpu19": {"hs": 274056463998651040},      # 274056 TH/s 矿池上报损坏 → 应剔除
}, "balance": 1.5}

v = D._twpool_view()
names = {w["name"]: w["th"] for w in v["workers"]}
ck("gpu16(139.83)保留", abs(names.get("gpu16", 0) - 139.83) < 0.1)
ck("rp2(260, 偏高但合理)保留", abs(names.get("rp2", 0) - 260.0) < 0.1)
ck("gpu19(损坏 274056)被剔除", "gpu19" not in names)
ck("总算力只含正常值 ≈399.83", abs(v["total_hashrate_th"] - 399.83) < 0.5)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
