#!/usr/bin/env python3
"""pearlfortune_worker_hashrates 解析 connections.reported_hashrate/stale/gpu; pool_worker_hashrates 按池路由。
monkeypatch S.request_json 不打网络。运行: python3 tests/test_pool_worker_hashrates.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
cfg={"prl_address":"prl1pX"}

S.request_json=lambda *a, **k: {"data":{"workers":[
    {"worker":"rig_abc123","reported_hashrate":269530616888726.12,"stale":False,"client_info":{"gpus":[{"model":"NVIDIA GeForce RTX 4090"}]}},
    {"worker":"rig_stale","reported_hashrate":140000000000000,"stale":True,"client_info":{"gpus":[]}},
    "junk",
]}}
S._pf_workers_cache={"data":None,"ts":0.0}
wh=S.pearlfortune_worker_hashrates(cfg)
ck("pf rig_abc123 ≈269.5TH", abs(wh.get("rig_abc123",{}).get("hashrate_th",0)-269.53)<0.1)
ck("pf gpu_info 带型号", wh.get("rig_abc123",{}).get("gpu_info")==[{"name":"NVIDIA GeForce RTX 4090"}])
ck("pf stale worker 算力=0", wh.get("rig_stale",{}).get("hashrate_th")==0.0)
ck("pf 非dict元素不崩", isinstance(wh, dict))

def _boom(*a, **k): raise RuntimeError("boom")
S.request_json=_boom; S._pf_workers_cache={"data":None,"ts":0.0}
ck("pf API 挂→{}", S.pearlfortune_worker_hashrates(cfg)=={})

S.request_json=lambda *a, **k: {"data":{"workers":[{"worker":"w","reported_hashrate":1e14,"stale":False}]}}
S._pf_workers_cache={"data":None,"ts":0.0}
ck("pool_worker_hashrates('pearlfortune') 有数据", "w" in S.pool_worker_hashrates(cfg,"pearlfortune"))
ck("pool_worker_hashrates('herominers')={}", S.pool_worker_hashrates(cfg,"herominers")=={})
ck("pool_worker_hashrates('unknown')={}", S.pool_worker_hashrates(cfg,"unknown")=={})
S._POOL_HASHRATE_FN["twpool"]=lambda c: {"tw_w":{"hashrate_th":140.0,"gpu_info":[]}}
ck("pool_worker_hashrates('twpool') 走注册 fn", S.pool_worker_hashrates(cfg,"twpool").get("tw_w",{}).get("hashrate_th")==140.0)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
