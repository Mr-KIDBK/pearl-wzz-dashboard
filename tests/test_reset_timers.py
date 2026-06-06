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
  ],
  # salad 用的是另一套 watch 状态(组级 + 逐实例)
  "salad_watch": {
      "gpu1:rp-salad-x": {"low_since_epoch": 800.0, "low_reason": "hashrate=0", "last_reallocate_epoch": 700.0},
  },
  "salad_instance_watch": {
      "gpu1:inst-aaa": {"low_since_epoch": 850.0, "low_reason": "hashrate=0", "last_reallocate_epoch": 0},
      "gpu2:inst-bbb": {"last_hashrate_th": 140.0},  # 无计时器
  },
}

n = S.reset_low_eff_timers(state)

a = state["rented"][0]
check("active 机器: low_efficiency_since_epoch 已清", "low_efficiency_since_epoch" not in a)
check("active 机器: low_efficiency_reason 已清", "low_efficiency_reason" not in a)
check("active 机器: zero_since_epoch 已清", "zero_since_epoch" not in a)
check("不动 host_switched_epoch(host 兜底状态保留)", a.get("host_switched_epoch") == 999.0)
check("无计时器的机器不受影响", state["rented"][1].get("last_hashrate_th") == 230.0)
# salad 组级 + 逐实例计时器也要清
sw = state["salad_watch"]["gpu1:rp-salad-x"]
check("salad_watch: low_since_epoch 已清", "low_since_epoch" not in sw)
check("salad_watch: low_reason 已清", "low_reason" not in sw)
check("salad_watch: 保留 last_reallocate_epoch(冷却状态)", sw.get("last_reallocate_epoch") == 700.0)
iw = state["salad_instance_watch"]["gpu1:inst-aaa"]
check("salad_instance_watch: low_since_epoch 已清", "low_since_epoch" not in iw)
check("salad 无计时器条目不受影响", state["salad_instance_watch"]["gpu2:inst-bbb"].get("last_hashrate_th") == 140.0)
check("返回清除计数 = 3(rented a + salad_watch 1 + instance_watch 1)", n == 3)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
