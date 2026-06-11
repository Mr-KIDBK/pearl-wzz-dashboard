#!/usr/bin/env python3
"""在跑机器计数测试: salad 只数 state=='running' 实例(排除 creating/downloading/allocating)。
运行: python3 tests/test_running_count.py"""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

# --- 单元: _is_running ---
check("非 salad 活跃租约(无 state)算在跑", D._is_running({}) is True)
check("salad running 算在跑", D._is_running({"state": "running"}) is True)
check("salad downloading 不算", D._is_running({"state": "downloading"}) is False)
check("salad creating 不算", D._is_running({"state": "creating"}) is False)
check("salad allocating 不算", D._is_running({"state": "allocating"}) is False)
check("salad stopping 不算", D._is_running({"state": "stopping"}) is False)

# --- active_rentals 透传 salad state(用真实 active_rentals + mock salad_live, 须在 monkeypatch 前) ---
D.salad_live = lambda aid="salad": {"instances": [
    {"id": "i1", "state": "running", "group": "gpu1"},
    {"id": "i2", "state": "downloading", "group": "gpu2"},
]}
ar0 = D.active_rentals("salad")
check("active_rentals salad 透传 state", [m.get("state") for m in ar0] == ["running", "downloading"])

# --- 集成: build_summary 只计在跑 ---
tmp = Path(tempfile.mkdtemp()); (tmp / "configs").mkdir()
D.ROOT = tmp
D.list_accounts = lambda: ["salad", "runpod"]
def fake_ar(acct):
    if acct == "salad":  # 2 running + 1 downloading + 1 creating = 真实在跑 2
        return [{"state": "running"}, {"state": "running"},
                {"state": "downloading"}, {"state": "creating"}]
    return [{}, {}]  # runpod 2 个活跃租约(无 state)
D.active_rentals = fake_ar
D.pool_data = lambda force=False: {}
D.tick_output = lambda pool=None: 0.0
D.update_output_snapshot = lambda merged_out=None: None   # 隔离: 不写真实 STATS_PATH
D.coin_price = lambda: 0.75
D.prl_address = lambda: ""

s = D.build_summary()
check("running_machines = 4 (salad 2 + runpod 2, 排除下载/创建中)", s["running_machines"] == 4)
check("running_by_platform.salad = 2", s["running_by_platform"].get("salad") == 2)
check("running_by_platform.runpod = 2", s["running_by_platform"].get("runpod") == 2)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
