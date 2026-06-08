#!/usr/bin/env python3
"""salad_group_names(组名: config 优先, 空则公共 API 发现) + 缓存访问器 salad_gpu_for/salad_real_balance。
运行: python3 tests/test_salad_group_names.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# --- salad_group_names: config 指定了组 → 直接用, 不调 API ---
D.read_config = lambda a: {"salad": {"organization_name": "o", "project_name": "p",
                                     "include_container_groups": ["gA", "gB"]}}
D.platform_of = lambda a: "salad"
D.read_env = lambda: {"SALAD_API_KEY": "k"}
D.key_var_for = lambda a: "SALAD_API_KEY"
called = {"n": 0}
def fake_get(url, key):
    called["n"] += 1
    return {"items": [{"name": "gX"}, {"name": "gY"}]}
D.salad_get = fake_get
ck("config 指定组 → 原样返回", D.salad_group_names("salad") == ["gA", "gB"])
ck("config 指定组 → 不调公共 API", called["n"] == 0)

# --- config 组为空 → 调公共 API 发现 ---
D.read_config = lambda a: {"salad": {"organization_name": "o", "project_name": "p",
                                     "include_container_groups": []}}
ck("空组 → 公共 API 发现", D.salad_group_names("salad") == ["gX", "gY"])
ck("空组 → 调了 API", called["n"] == 1)

# --- project_name 为空 → 回退 "default" 仍能发现(与 start_portal_manager 一致) ---
D.read_config = lambda a: {"salad": {"organization_name": "o", "project_name": "",
                                     "include_container_groups": []}}
got = {"url": None}
def fake_get_default(url, key):
    got["url"] = url
    return {"items": [{"name": "gZ"}]}
D.salad_get = fake_get_default
ck("空 project_name → 仍发现(回退 default)", D.salad_group_names("salad") == ["gZ"])
ck("空 project_name → URL 用 /projects/default/containers", "/projects/default/containers" in (got["url"] or ""))

# --- 访问器: 新鲜缓存命中, 过期/缺失回退 ---
D._salad_gpu.clear(); D._salad_balance.clear()
D._salad_gpu["salad"] = {"data": {"iA": "RTX 5070 Ti"}, "ts": time.time()}
D._salad_balance["salad"] = {"data": 20.46, "ts": time.time()}
ck("salad_gpu_for 命中", D.salad_gpu_for("salad") == {"iA": "RTX 5070 Ti"})
ck("salad_real_balance 命中", D.salad_real_balance("salad") == 20.46)
ck("缺账号 gpu → {}", D.salad_gpu_for("salad-2") == {})
ck("缺账号 balance → None", D.salad_real_balance("salad-2") is None)
# 过期(ts 远古) → 回退
D._salad_gpu["salad"]["ts"] = 0; D._salad_balance["salad"]["ts"] = 0
ck("过期 gpu → {}", D.salad_gpu_for("salad") == {})
ck("过期 balance → None", D.salad_real_balance("salad") is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
