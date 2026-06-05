#!/usr/bin/env python3
"""看板卡顿修复的测试: salad_live 并发拉取 + serve-stale 缓存 + 后台 force 刷新。
全部用 mock, 不打真实网络。运行: python3 tests/test_salad_perf.py"""
import os, sys, json, time, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

# ---------- B: _salad_compute 并发拉取(正确性 + 提速) ----------
tmp = Path(tempfile.mkdtemp()); (tmp / "configs").mkdir()
D.ROOT = tmp
(tmp / "configs" / "config.salad.json").write_text(json.dumps({"salad": {
    "enabled": True, "organization_name": "org", "project_name": "proj",
    "include_container_groups": ["g1", "g2", "g3"],
}}))
os.environ["SALAD_API_KEY"] = "k"
D._salad.clear(); D._gpucls.clear()

SLEEP = 0.2
def fake_salad_get(url, key):
    time.sleep(SLEEP)
    if url.endswith("/gpu-classes"):
        return {"items": []}
    if url.endswith("/instances"):
        g = url.split("/containers/")[1].split("/")[0]
        return {"instances": [{"instance_id": g + "-i", "machine_id": g + "-m",
                               "state": "running", "update_time": "2026-06-05T00:00:00Z"}]}
    g = url.split("/containers/")[1]
    return {"current_state": {"instance_status_counts": {"running": 1}},
            "priority": "medium", "container": {"resources": {"gpu_classes": []}}}
D.salad_get = fake_salad_get
D.pool_data = lambda force=False: {}

t0 = time.time(); res = D.salad_live("salad", force=True); dt = time.time() - t0
ids = sorted(i["id"] for i in res.get("instances", []))
check("3 组实例全部返回", ids == ["g1-i", "g2-i", "g3-i"])
check("组名正确", sorted(i["group"] for i in res["instances"]) == ["g1", "g2", "g3"])
# 7 次调用(1 gpu-classes + 3 组 × 2) 串行 = 1.4s; 并发应远小于
check(f"并发提速 (实测 {dt:.2f}s, 串行约 1.4s, 期望<0.9s)", dt < 0.9)

# ---------- A: serve-stale 缓存语义 ----------
D._salad.clear()
calls = [0]
def fake_compute(aid):
    calls[0] += 1
    return {"instances": [{"id": "sent-" + aid}]}
D._salad_compute = fake_compute

check("冷启动: 无缓存 → 计算", D.salad_live("X")["instances"][0]["id"] == "sent-X" and calls[0] == 1)
D.salad_live("X");                       check("热缓存: 不重算", calls[0] == 1)
D.salad_live("X", force=True);           check("force=True: 强制重算", calls[0] == 2)
D._salad["X"]["ts"] = time.time() - 5;   D.salad_live("X")
check("轻微陈旧(<兜底上限): 仍读缓存不阻塞", calls[0] == 2)
D._salad["X"]["ts"] = time.time() - (D.SALAD_STALE_MAX + 10); D.salad_live("X")
check("超兜底上限: 同步重算(防后台线程挂)", calls[0] == 3)

# ---------- C: 后台 _refresh_once 用 force 真刷新 ----------
seen = []
D.salad_live = lambda aid, force=False: seen.append(("salad_live", aid, force))
D.platform_balance = lambda aid, force=False: seen.append(("bal", aid, force))
D.pool_data = lambda force=False: seen.append(("pool", force))
D._refresh_once()
check("后台刷新矿池(force)", ("pool", True) in seen)
check("后台 force 刷新 salad 账号", ("salad_live", "salad", True) in seen)
check("后台 force 刷新余额", ("bal", "salad", True) in seen)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
