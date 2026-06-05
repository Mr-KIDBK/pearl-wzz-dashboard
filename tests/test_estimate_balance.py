#!/usr/bin/env python3
"""estimate_manual_balance 的单元测试(纯标准库, 无 pytest)。
运行: python3 tests/test_estimate_balance.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import estimate_manual_balance as est

def check(name, got, want):
    if got != want:
        print(f"  ✗ {name}: got {got!r}, want {want!r}")
        return 1
    print(f"  ✓ {name}")
    return 0

fails = 0
# 无时间流逝 → 余额不变
fails += check("no elapsed", est(100.0, 1000.0, 10.0, 1000.0), 100.0)
# 1 小时 @ 10/h, 余额 100 → 90
fails += check("1h at 10/h", est(100.0, 1000.0, 10.0, 1000.0 + 3600), 90.0)
# 递减不为负: 余额 5, burn 10, 1h → 0(不是 -5)
fails += check("clamp at zero", est(5.0, 1000.0, 10.0, 1000.0 + 3600), 0.0)
# burn=0 → 不论多久余额不变
fails += check("zero burn", est(100.0, 1000.0, 0.0, 1000.0 + 99999), 100.0)
# 保留两位小数: 100 - 3.333*1h ≈ 96.67
fails += check("rounding", est(100.0, 1000.0, 3.333, 1000.0 + 3600), 96.67)
# asof 在未来(now<asof) → elapsed 视为 0, 不增不减
fails += check("future asof", est(100.0, 2000.0, 10.0, 1000.0), 100.0)
# 半小时 @ 20/h → 减 10
fails += check("half hour", est(50.0, 0.0, 20.0, 1800.0), 40.0)

if fails:
    print(f"\n{fails} 个断言失败")
    sys.exit(1)
print("\n全部通过")
