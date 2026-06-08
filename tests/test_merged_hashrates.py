#!/usr/bin/env python3
"""merged_worker_hashrates 测试: 双池合并取最大 + 单池故障容错。
运行: python3 tests/test_merged_hashrates.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
S._POOL_HASHRATE_FN={
    "pearlhash": lambda cfg:{"wA":{"hashrate_th":100.0},"wShared":{"hashrate_th":50.0}},
    "twpool":    lambda cfg:{"wB":{"hashrate_th":200.0},"wShared":{"hashrate_th":80.0}},
}
m=S.merged_worker_hashrates({"monitor_pools":["pearlhash","twpool"]})
ck("两池 worker 都在", set(m)=={"wA","wB","wShared"})
ck("同名取最大(80>50)", m["wShared"]["hashrate_th"]==80.0)
# 单池抛错不崩, 另一池仍在
S._POOL_HASHRATE_FN["twpool"]=lambda cfg:(_ for _ in ()).throw(Exception("net"))
m2=S.merged_worker_hashrates({"monitor_pools":["pearlhash","twpool"]})
ck("一池故障跳过, 另一池仍在", "wA" in m2 and "wB" not in m2)
# 默认 monitor_pools = 全部池
m3=S.merged_worker_hashrates({})
ck("默认查全部池(含 wA)", "wA" in m3)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
