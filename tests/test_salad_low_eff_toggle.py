#!/usr/bin/env python3
"""run_salad_cycle 的 salad_low_efficiency_enabled 开关:
false 时仍计算并写入算力(供 dashboard 显示), 但跳过低效判断、绝不 reallocate。
运行: python3 tests/test_salad_low_eff_toggle.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n)
    fails += 0 if c else 1

NAME = "gpu1"
IID = "inst-123"
NOW = 1_000_000.0

def setup_mocks():
    """mock run_salad_cycle 的全部外部依赖; 返回 reallocate 调用记录 list。"""
    group = {"name": NAME,
             "container": {"image": "docker.io/conishc/pearl-miner:twpool-v1.9.0-auto"},
             "current_state": {"instance_status_counts": {"running_count": 1}}}
    S.list_salad_container_groups = lambda config: [group]
    S.salad_group_running_count = lambda g: 1
    S.salad_group_worker_name = lambda g: NAME
    S.salad_min_hashrate_for_group = lambda g, cfg: (220.0, "RTX 4090", False)
    S.list_salad_instances = lambda config, name: [
        {"id": IID, "state": "running", "started": True, "machine_id": "m1"}]
    S.salad_query_instance_hashrates = lambda config, name, lookback: {}  # 无日志算力 → missing_log_zero
    S.merged_worker_hashrates = lambda config: {}                          # 矿池兜底也查不到
    S.object_contains_text = lambda obj, text: False                       # 非 alphapool 组
    S.epoch_now = lambda: NOW
    S.log = lambda *a, **k: None
    S.notify = lambda *a, **k: None
    calls = []
    def fake_realloc(config, name, instance_id):
        calls.append((name, instance_id)); return {"ok": True}
    S.reallocate_salad_instance = fake_realloc
    return calls

def make_config(flag=None):
    salad = {"enabled": True, "low_efficiency_stop_seconds": 300,
             "reallocate_cooldown_seconds": 600, "hashrate_watch_interval_seconds": 30}
    if flag is not None:
        salad["salad_low_efficiency_enabled"] = flag
    return {"salad": salad, "prl_address": "prl1x", "prl_host": "h", "pool": "twpool"}

def make_state():
    # 预置该实例低效计时在很久以前 + 无 cooldown → 满足 reallocate 触发条件
    return {"salad_instance_watch": {f"{NAME}:{IID}": {"low_since_epoch": NOW - 100000.0}}}

# --- 缺省(无 flag)→ 低效启用 → reallocate 调用一次, 且记录算力 ---
calls = setup_mocks(); st = make_state()
S.run_salad_cycle(make_config(None), st, live=True)
ck("缺省: reallocate 调用一次", len(calls) == 1 and calls[0] == (NAME, IID))
ck("缺省: 仍记录 last_hashrate_th=0.0",
   st["salad_instance_watch"][f"{NAME}:{IID}"].get("last_hashrate_th") == 0.0)

# --- flag=True → 同缺省 ---
calls = setup_mocks(); st = make_state()
S.run_salad_cycle(make_config(True), st, live=True)
ck("True: reallocate 调用一次", len(calls) == 1)

# --- flag=False → reallocate 一次都不调, 但算力仍记录, 低效计时清除 ---
calls = setup_mocks(); st = make_state()
S.run_salad_cycle(make_config(False), st, live=True)
e = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("False: reallocate 不调用", len(calls) == 0)
ck("False: 仍记录 last_hashrate_th=0.0", e.get("last_hashrate_th") == 0.0)
ck("False: low_since_epoch 被清除", "low_since_epoch" not in e)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
