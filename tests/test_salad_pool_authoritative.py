#!/usr/bin/env python3
"""run_salad_cycle 池权威逐实例低效判定:
①池在线≥门槛不reallocate ②日志正常但池离线+超时→reallocate(核心) ③新实例宽限内不杀
④twpool API 挂(merged 空)→退日志不误杀 ⑤salad_low_efficiency_enabled=false 不判。
运行: python3 tests/test_salad_pool_authoritative.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

ADDR = "prl1x"; NAME = "gpu1"; IID = "inst-1"; MID = "m-abc"; NOW = 1_000_000.0
GRACE = 600; LOWSEC = 300

def setup(*, log_hr, merged, flag=True, first_seen=NOW - 5000, low_since=NOW - 5000, pool_seen=NOW - 5000, machine_id=MID):
    """mock run_salad_cycle 依赖; 返回 (config, state, reallocate 调用记录)。
    log_hr=None 表示无日志算力; merged 是 merged_worker_hashrates 返回值。"""
    group = {"name": NAME, "container": {"image": "docker.io/mrkidbk/pearl-miner-twpool:v1.9.1"},
             "current_state": {"instance_status_counts": {"running_count": 1}}}
    S.list_salad_container_groups = lambda config: [group]
    S.salad_group_running_count = lambda g: 1
    S.salad_group_worker_name = lambda g: NAME
    S.salad_min_hashrate_for_group = lambda g, cfg: (220.0, "RTX 4090", False)
    S.list_salad_instances = lambda config, name: [
        {"id": IID, "state": "running", "started": True, "machine_id": machine_id}]
    if log_hr is None:
        S.salad_query_instance_hashrates = lambda config, name, lb: {}
    else:
        S.salad_query_instance_hashrates = lambda config, name, lb: {
            IID: {"hashrate_th": log_hr, "machine_id": machine_id, "gpu_name": "RTX 4090"}}
    S.merged_worker_hashrates = lambda config: merged
    # 按池路由后 run_salad_cycle 改用 pool_worker_hashrates(config, pool_id); 单组镜像=twpool →
    # current_pool='twpool'(池权威, 行为与改前一致)。把单池查询路由到同一 merged mock。
    S.pool_worker_hashrates = lambda config, pid: merged
    S.object_contains_text = lambda obj, text: False
    S.epoch_now = lambda: NOW
    S.log = lambda *a, **k: None
    S.notify = lambda *a, **k: None
    calls = []
    S.reallocate_salad_instance = lambda config, name, iid: calls.append((name, iid)) or {"ok": True}
    cfg = {"enabled": True, "low_efficiency_stop_seconds": LOWSEC,
           "reallocate_cooldown_seconds": 600, "hashrate_watch_interval_seconds": 30,
           "salad_new_instance_grace_seconds": GRACE, "salad_low_efficiency_enabled": flag}
    config = {"salad": cfg, "prl_address": ADDR, "pool": "twpool"}
    entry = {"first_seen_epoch": first_seen, "low_since_epoch": low_since}
    if pool_seen is not None:
        # 按池路由后 pool_seen 用按池作用域键 pool_seen_by[pool_id]; 单组=twpool。
        # (legacy 标量 pool_seen_epoch 已被 use_pool 忽略 —— 防迁池误杀的安全改进。)
        entry["pool_seen_by"] = {"twpool": pool_seen}
    state = {"salad_instance_watch": {f"{NAME}:{IID}": entry}}
    return config, state, calls

ONLINE = {f"{ADDR}.{NAME}_{MID}": {"hashrate_th": 250.0, "gpu_info": []}}   # 该实例在池且高算力
OFFLINE = {f"{ADDR}.gpu99_other": {"hashrate_th": 250.0, "gpu_info": []}}   # API 正常但无本实例 worker
APIDOWN = {}                                                               # merged 空 = API 挂/无数据

# ① 池在线≥门槛 → 不 reallocate, 清 low
config, st, calls = setup(log_hr=250.0, merged=ONLINE)
S.run_salad_cycle(config, st, live=True)
e = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("①池在线≥门槛 不reallocate", len(calls) == 0)
ck("①清 low_since_epoch", "low_since_epoch" not in e)

# ② 日志正常(250) 但池离线 + 超时 → reallocate(核心)
config, st, calls = setup(log_hr=250.0, merged=OFFLINE)
S.run_salad_cycle(config, st, live=True)
ck("②日志正常但池离线+超时 → reallocate", calls == [(NAME, IID)])

# ③ 新实例(first_seen=NOW, 宽限内)即使池离线 → 不 reallocate
config, st, calls = setup(log_hr=250.0, merged=OFFLINE, first_seen=NOW)
S.run_salad_cycle(config, st, live=True)
ck("③宽限内 不reallocate", len(calls) == 0)

# ④ twpool API 挂(merged 空)+ 日志健康(250≥220) → 退日志判定, 不误杀
config, st, calls = setup(log_hr=250.0, merged=APIDOWN)
S.run_salad_cycle(config, st, live=True)
ck("④API挂 退日志(健康) 不reallocate", len(calls) == 0)

# ⑤ flag=false → 不判, 不 reallocate(即使池离线+超时)
config, st, calls = setup(log_hr=250.0, merged=OFFLINE, flag=False)
S.run_salad_cycle(config, st, live=True)
ck("⑤flag=false 不reallocate", len(calls) == 0)

# ⑥ 从未在池出现过(pool_seen=None)+ 日志健康 → 退日志判定, 不杀(防新镜像未铺开/worker名不匹配误杀)
config, st, calls = setup(log_hr=250.0, merged=OFFLINE, pool_seen=None)
S.run_salad_cycle(config, st, live=True)
ck("⑥从未在池+日志健康 不reallocate(防误杀)", len(calls) == 0)

# ⑦ 无 machine_id + 池离线 + 日志健康 → 退日志, 不杀
config, st, calls = setup(log_hr=250.0, merged=OFFLINE, machine_id=None)
S.run_salad_cycle(config, st, live=True)
ck("⑦无machine_id 退日志 不reallocate", len(calls) == 0)

# 显示口径: last_hashrate_th 仍是日志 window(250); pool_hashrate_th=None(离线)
config, st, calls = setup(log_hr=250.0, merged=OFFLINE)
S.run_salad_cycle(config, st, live=True)
e = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("显示 last_hashrate_th=日志window 250", e.get("last_hashrate_th") == 250.0)
ck("另存 pool_hashrate_th=None(离线)", e.get("pool_hashrate_th") is None)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
