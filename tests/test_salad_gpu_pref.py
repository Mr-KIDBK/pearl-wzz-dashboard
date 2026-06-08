#!/usr/bin/env python3
"""_salad_compute 优先用 portal gpu_class 定 GPU + 单价(按组档价), 无缓存时回退。
运行: python3 tests/test_salad_gpu_pref.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# 配置: 启用 salad, 指定一个组
D.read_config = lambda a: {"salad": {"enabled": True, "organization_name": "o", "project_name": "p",
                                     "include_container_groups": ["g1"], "base_url": "https://api.salad.com/api/public"}}
D.platform_of = lambda a: "salad"
D.read_env = lambda: {"SALAD_API_KEY": "k"}
D.key_var_for = lambda a: "SALAD_API_KEY"
D.read_state = lambda a: {}
D.pool_data = lambda force=False: {}          # 无 pearlhash worker → pgpu 兜底为空
# gpu-classes 价表: medium 档 RTX 5070 Ti = 0.22
D.salad_gpu_prices = lambda base, org, key: {"uuid1": {"name": "RTX 5070 Ti", "prices": {"medium": 0.22}}}
def fake_salad_get(url, key):
    if url.endswith("/instances"):
        return {"instances": [{"instance_id": "iA", "machine_id": "mA", "state": "running"}]}
    # 组详情: medium 档, gpu_classes=[uuid1]
    return {"current_state": {"instance_status_counts": {"running_count": 1}}, "priority": "medium",
            "container": {"image": "docker.io/conishc/pearl-miner:twpool", "resources": {"gpu_classes": ["uuid1"]}}}
D.salad_get = fake_salad_get

# --- 有 gpu_class 缓存 → GPU=清洗后的 gpu_class, 单价=组档价 ---
D._salad_gpu.clear()
D._salad_gpu["salad"] = {"data": {"iA": "NVIDIA GeForce RTX 5070 Ti"}, "ts": time.time()}
res = D._salad_compute("salad")
inst = (res.get("instances") or [{}])[0]
ck("GPU 用 gpu_class(去 NVIDIA GeForce)", inst.get("gpu") == "RTX 5070 Ti")
ck("单价按组档价=0.22", inst.get("price") == 0.22)
ck("price_label=$0.220/h", inst.get("price_label") == "$0.220/h")

# --- 无缓存 → 回退(无 pool worker → gpu='?') ---
D._salad_gpu.clear()
res2 = D._salad_compute("salad")
inst2 = (res2.get("instances") or [{}])[0]
ck("无缓存回退 GPU='?'", inst2.get("gpu") == "?")

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
