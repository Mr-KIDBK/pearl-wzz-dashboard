#!/usr/bin/env python3
"""run_salad_cycle 用稳定的 machine_id 关联"日志算力↔实例":
salad 实例 API 偶发返回 instance_id=None, 但 machine_id 稳定且日志里也有 machine_id。
旧代码按 instance_id 关联(还会 `if not instance_id: continue` 跳过)→ 算力丢失显示 0。
新代码: 无 instance_id 不跳过; 日志算力按 machine_id 关联; watch key 用 instance_id 或 machine_id。
运行: python3 tests/test_salad_machine_id_join.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

NAME = "gpu1"; MID = "m-x"; NOW = 1_000_000.0

def base_mocks(*, api_instance, log_rates):
    group = {"name": NAME, "container": {"image": "docker.io/mrkidbk/pearl-miner-twpool:v1.9.1"},
             "current_state": {"instance_status_counts": {"running_count": 1}}}
    S.list_salad_container_groups = lambda config: [group]
    S.salad_group_running_count = lambda g: 1
    S.salad_group_worker_name = lambda g: NAME
    S.salad_min_hashrate_for_group = lambda g, cfg: (220.0, "RTX 4090", False)
    S.list_salad_instances = lambda config, name: [api_instance]
    S.salad_query_instance_hashrates = lambda config, name, lb: log_rates
    S.merged_worker_hashrates = lambda config: {}
    S.object_contains_text = lambda o, t: False
    S.epoch_now = lambda: NOW
    S.log = lambda *a, **k: None
    S.notify = lambda *a, **k: None
    S.reallocate_salad_instance = lambda *a, **k: {"ok": True}
    cfg = {"enabled": True, "low_efficiency_stop_seconds": 300, "reallocate_cooldown_seconds": 600,
           "hashrate_watch_interval_seconds": 30, "salad_low_efficiency_enabled": False}
    return {"salad": cfg, "prl_address": "prl1x", "pool": "twpool"}

# --- 核心: API instance_id=None, 日志按"另一个"日志id键但 machine_id 匹配 ---
config = base_mocks(
    api_instance={"id": None, "instance_id": None, "machine_id": MID, "state": "running", "started": True},
    log_rates={"log-abc-different-id": {"hashrate_th": 139.0, "machine_id": MID, "gpu_name": "RTX 4090"}})
st = {"salad_instance_watch": {}}
S.run_salad_cycle(config, st, live=True)
iw = st["salad_instance_watch"]
ck("无 instance_id 实例不被跳过(有 watch 条目)", len(iw) == 1)
ck(f"按 machine_id 建 key {NAME}:{MID}", f"{NAME}:{MID}" in iw)
ck("日志算力按 machine_id 关联 = 139", list(iw.values())[0].get("last_hashrate_th") == 139.0)

# --- 回归: 正常有 instance_id(且日志同 id)→ 仍正确记录 ---
config = base_mocks(
    api_instance={"id": "iid-1", "machine_id": MID, "state": "running", "started": True},
    log_rates={"iid-1": {"hashrate_th": 142.0, "machine_id": MID, "gpu_name": "RTX 4090"}})
st = {"salad_instance_watch": {}}
S.run_salad_cycle(config, st, live=True)
iw = st["salad_instance_watch"]
ck("有 instance_id 时 key=group:instance_id", f"{NAME}:iid-1" in iw)
ck("正常记录算力 = 142", iw.get(f"{NAME}:iid-1", {}).get("last_hashrate_th") == 142.0)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
