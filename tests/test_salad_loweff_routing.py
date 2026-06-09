#!/usr/bin/env python3
"""run_salad_cycle 按池路由: pearlfortune 池权威 / herominers 恒走容器日志 / 迁池安全 / 池挂兜底。
端到端跑 run_salad_cycle, monkeypatch 其依赖(沿用 tests/test_salad_pool_authoritative.py 的 mock 模式:
salad_min_hashrate_for_group 直接给 (220,'RTX 4090',False), salad_query_instance_hashrates / pool_worker_hashrates
/ reallocate_salad_instance)。门槛 220 + RTX 4090: 250/240 = 健康, 0 = 低效。
运行: python3 tests/test_salad_loweff_routing.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

ADDR = "prl1pX"; NAME = "gpu1"; IID = "i1"; MID = "MID"
NOW = [1_000_000.0]
LOWSEC = 300; GRACE = 600
S.epoch_now = lambda: NOW[0]
S.log = lambda *a, **k: None
S.notify = lambda *a, **k: None
S.object_contains_text = lambda obj, text: False   # 不触发 alphapool 路径
S.salad_min_hashrate_for_group = lambda g, cfg: (220.0, "RTX 4090", False)  # 4090 门槛 220
S.salad_group_running_count = lambda g: 1
S.salad_group_worker_name = lambda g: NAME
realloc = []
S.reallocate_salad_instance = lambda config, name, iid: realloc.append((name, iid)) or {"ok": True}


def mk_cfg(image):
    return {"prl_address": ADDR, "pool": "pearlfortune",
            "salad": {"enabled": True, "observe_enabled": True, "salad_low_efficiency_enabled": True,
                      "low_efficiency_stop_seconds": LOWSEC, "reallocate_cooldown_seconds": 600,
                      "salad_new_instance_grace_seconds": GRACE, "hashrate_watch_interval_seconds": 30,
                      "min_hashrate_th": {"RTX 4090": 220}, "default_min_hashrate_th": 220,
                      "per_model_threshold_enabled": False, "_image": image}}


def setup(image):
    S.list_salad_container_groups = lambda config: [
        {"name": NAME, "container": {"image": image, "resources": {"gpu_classes": ["c4090"]}},
         "current_state": {"instance_status_counts": {"running_count": 1}}}]
    S.list_salad_instances = lambda config, name: [
        {"instance_id": IID, "id": IID, "machine_id": MID, "state": "running", "started": True}]


def log_rate(th):
    S.salad_query_instance_hashrates = lambda config, name, lb: {
        IID: {"machine_id": MID, "hashrate_th": th, "gpu_name": "RTX 4090"}}


def aged_state():
    # first_seen 远早于 now → 已过宽限期(GRACE 600); 每个用例独立 state
    return {"salad_instance_watch": {f"{NAME}:{IID}": {"first_seen_epoch": NOW[0] - 100000}},
            "salad_watch": {}}


# --- 用例 A: pearlfortune 池权威, pool_hr=250≥220 → 健康(不杀)
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.1"); setup(cfg["salad"]["_image"])
S.pool_worker_hashrates = lambda config, pid: ({"rig_MID_x": {"hashrate_th": 250.0, "gpu_info": [{"name": "RTX 4090"}]}} if pid == "pearlfortune" else {})
log_rate(0.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True)
ea = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("A pf 池250≥220 健康不杀", realloc == [])
ck("A pool_seen_by[pearlfortune] 已记", (ea.get("pool_seen_by") or {}).get("pearlfortune") == NOW[0])
ck("A pool_hashrate_th=250(池权威显示)", ea.get("pool_hashrate_th") == 250.0)

# --- 用例 B: pearlfortune 曾seen后池缺席 → 池权威判0<220 → 持续到 reallocate(核心)。
#     日志故意给健康 250: 证明是"池权威离线判0"在杀, 而非日志兜底(若退日志, 250≥220 不会杀)。
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.1"); setup(cfg["salad"]["_image"])
seen = [True]
# seen=True: 本实例 worker 在表(健康); seen=False: 池 API 仍在线(列别的 worker)但本 machine_id 缺席=离线。
# 注意要返回非空表(含别的 worker)以使 pool_api_ok=True —— 区分"API挂(空)"与"本机离线(在表但无此 worker)"。
S.pool_worker_hashrates = lambda config, pid: (({"rig_MID_x": {"hashrate_th": 250.0, "gpu_info": []}} if seen[0] else {"rig_OTHER_y": {"hashrate_th": 250.0, "gpu_info": []}}) if pid == "pearlfortune" else {})
log_rate(250.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True)          # seen=True → pool_seen[pearlfortune] 记下, 健康
seen[0] = False; NOW[0] += 400            # 池在线但本机缺席, 推进
S.run_salad_cycle(cfg, st, True)          # 池权威离线判0 → low_since(日志 250 健康但被池权威覆盖)
NOW[0] += 400
S.run_salad_cycle(cfg, st, True)          # 持续低 > 300s → reallocate
ck("B pf 曾seen后缺席→reallocate(池权威覆盖健康日志)", realloc == [(NAME, IID)])

# --- 用例 C: herominers 恒走日志判定: 即便池给0也不作权威; 日志健康(240≥220)→ 不杀
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/mrkidbk/pearl-miner-herominers:v3.3.6"); setup(cfg["salad"]["_image"])
S.pool_worker_hashrates = lambda config, pid: {"rig_MID_x": {"hashrate_th": 0.0, "gpu_info": []}}  # 给0, herominers 不作权威
log_rate(240.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True)
ec = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("C herominers 日志健康→不杀(不被池0误杀)", realloc == [])
ck("C herominers 未记 pool_seen_by(非权威)", not ec.get("pool_seen_by"))

# --- 用例 D: 迁池安全: herominers 实例带旧 twpool pool_seen(+旧标量) → 不误杀
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/mrkidbk/pearl-miner-herominers:v3.3.6"); setup(cfg["salad"]["_image"])
S.pool_worker_hashrates = lambda config, pid: {}     # herominers 无权威
log_rate(240.0); st = aged_state()
st["salad_instance_watch"][f"{NAME}:{IID}"]["pool_seen_by"] = {"twpool": NOW[0] - 50}  # 旧 twpool seen 残留
st["salad_instance_watch"][f"{NAME}:{IID}"]["pool_seen_epoch"] = NOW[0] - 50           # 旧标量残留
realloc.clear()
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True)
ck("D 迁池: herominers 带旧 twpool seen 不误杀", realloc == [])

# --- 用例 E: 池 API 挂(pool_worker_hashrates→{}) → pearlfortune 实例退日志; 日志健康(240)不杀
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.1"); setup(cfg["salad"]["_image"])
S.pool_worker_hashrates = lambda config, pid: {}
log_rate(240.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True); NOW[0] += 400
S.run_salad_cycle(cfg, st, True)
ck("E 池挂→日志兜底, 日志健康不杀", realloc == [])

# --- 用例 F: pearlhash 池权威(恢复 Phase-2 遗漏): pool_hr 健康(250≥220) → 不杀, pool_seen_by 记 pearlhash
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/kuzigmgm/pearl-miner:v11"); setup(cfg["salad"]["_image"])
S.pool_worker_hashrates = lambda config, pid: ({"rig_MID_x": {"hashrate_th": 250.0, "gpu_info": [{"name": "RTX 4090"}]}} if pid == "pearlhash" else {})
log_rate(0.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True)
ef = st["salad_instance_watch"][f"{NAME}:{IID}"]
ck("F pearlhash 池250≥220 健康不杀", realloc == [])
ck("F pearlhash pool_seen_by[pearlhash] 已记(pool_authoritative=True)", (ef.get("pool_seen_by") or {}).get("pearlhash") == NOW[0])

# --- 用例 G: pearlhash 曾seen后池缺席 → 池权威判0<220 → reallocate(与用例 B 对称)
NOW[0] = 1_000_000.0
cfg = mk_cfg("docker.io/kuzigmgm/pearl-miner:v11"); setup(cfg["salad"]["_image"])
seen_ph = [True]
S.pool_worker_hashrates = lambda config, pid: (({"rig_MID_x": {"hashrate_th": 250.0, "gpu_info": []}} if seen_ph[0] else {"rig_OTHER_y": {"hashrate_th": 250.0, "gpu_info": []}}) if pid == "pearlhash" else {})
log_rate(250.0); st = aged_state(); realloc.clear()
S.run_salad_cycle(cfg, st, True)          # seen=True → pool_seen[pearlhash] 记下, 健康
seen_ph[0] = False; NOW[0] += 400         # 池在线但本机缺席, 推进
S.run_salad_cycle(cfg, st, True)          # 池权威离线判0 → low_since
NOW[0] += 400
S.run_salad_cycle(cfg, st, True)          # 持续低 > 300s → reallocate
ck("G pearlhash 曾seen后缺席→reallocate(池权威覆盖健康日志)", realloc == [(NAME, IID)])

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
