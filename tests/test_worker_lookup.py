#!/usr/bin/env python3
"""矿池 worker 查找测试: 精确匹配失败时应 fallback 到前缀匹配(矿机镜像会在 worker 名后追加 -hash 后缀)。
运行: python3 tests/test_worker_lookup.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

# 矿池返回的是带后缀的名字
pool = {
    "rp1-runpod-nvidia-geforce-rtx-4090-1780773313-24b713fb1eed":
        {"hashrate_th": 240.5, "ip": "1.2.3.4", "version": "v11", "gpu_info": []},
    "auto-vast-rtx-5090-39547945-b467b490dc9e":
        {"hashrate_th": 310.0, "ip": "5.6.7.8", "version": "v11", "gpu_info": []},
    "other-worker":
        {"hashrate_th": 100.0, "ip": "9.0.0.1", "version": "v11", "gpu_info": []},
}

# 精确匹配仍然工作
check("精确匹配(池内有对应)", S.lookup_worker(pool, "other-worker") is not None)
check("精确匹配返回正确算力", S.lookup_worker(pool, "other-worker")["hashrate_th"] == 100.0)

# sniper 存的是不带后缀的短名
check("短名精确匹配 miss → 返回 None(旧行为)", pool.get("rp1-runpod-nvidia-geforce-rtx-4090-1780773313") is None)

# 新: lookup_worker 做前缀匹配
info = S.lookup_worker(pool, "rp1-runpod-nvidia-geforce-rtx-4090-1780773313")
check("前缀匹配: 短名能找到带后缀的 runpod worker", info is not None)
check("前缀匹配返回正确算力", info is not None and info["hashrate_th"] == 240.5)

info2 = S.lookup_worker(pool, "auto-vast-rtx-5090-39547945")
check("前缀匹配: vast worker(后缀不同格式)", info2 is not None and info2["hashrate_th"] == 310.0)

# 歧义: 多个 worker 都以该前缀开头 → 不匹配(避免误判)
pool2 = {
    "prefix-aaa": {"hashrate_th": 1.0, "ip": "", "version": "", "gpu_info": []},
    "prefix-bbb": {"hashrate_th": 2.0, "ip": "", "version": "", "gpu_info": []},
}
check("前缀歧义(多个候选) → 返回 None", S.lookup_worker(pool2, "prefix") is None)

# 完全不存在
check("不存在的 worker → None", S.lookup_worker(pool, "nonexistent") is None)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
