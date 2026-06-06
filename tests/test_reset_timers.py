#!/usr/bin/env python3
"""reset_low_eff_timers 测试: sniper 重启时清空低效/零算力计时器,
让每台在租机器重新获得完整观测窗口(不继承重启前的旧计时器)。
运行: python3 tests/test_reset_timers.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

state = {"rented": [
    {"contract_id": "a", "active": True,
     "low_efficiency_since_epoch": 1000.0, "low_efficiency_reason": "hashrate=0",
     "zero_since_epoch": 1000.0, "host_switched_epoch": 999.0},
    {"contract_id": "b", "active": True, "last_hashrate_th": 230.0},  # 无计时器
    {"contract_id": "c", "active": False, "low_efficiency_since_epoch": 500.0},  # 已停用
]}

n = S.reset_low_eff_timers(state)

a = state["rented"][0]
check("active 机器: low_efficiency_since_epoch 已清", "low_efficiency_since_epoch" not in a)
check("active 机器: low_efficiency_reason 已清", "low_efficiency_reason" not in a)
check("active 机器: zero_since_epoch 已清", "zero_since_epoch" not in a)
check("不动 host_switched_epoch(host 兜底状态保留)", a.get("host_switched_epoch") == 999.0)
check("无计时器的机器不受影响", state["rented"][1].get("last_hashrate_th") == 230.0)
check("返回清除计数 = 1(只有 a 有低效计时器)", n == 1)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
