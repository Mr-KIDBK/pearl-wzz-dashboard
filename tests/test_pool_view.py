#!/usr/bin/env python3
"""pool_view(which): pearlhash/twpool/merged 映射 workers/总算力/矿池余额/error。
monkeypatch pool_data/twpool_data 不打网络。运行: python3 tests/test_pool_view.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
addr="prl1pX"
D.prl_address=lambda: addr
D.pool_data=lambda force=False: {"balance":10.0,
    "connected_workers":[{"worker_name":"wph","ip":"1.2.3.4",
        "gpu_info":[{"name":"RTX 4090","hashrate":250000000000000}]}]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,
    "reported":{f"{addr}.gpu10":{"hs":140000000000000}}}
# 新池默认 mock 为空(避免真打网络; 让 merged 余额仍=ph+tw)
D.herominers_data=lambda force=False: {"error":"Not found"}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"credits":{"sum_amount_atomic":0}}},"connections":{"data":{"workers":[]}}}

ph=D.pool_view("pearlhash")
ck("pearlhash 总算力≈250", abs(ph["total_hashrate_th"]-250)<1)
ck("pearlhash worker=wph", [w["name"] for w in ph["workers"]]==["wph"])
ck("pearlhash 矿池余额=10", ph["pool_balance"]==10.0)

tw=D.pool_view("twpool")
ck("twpool 总算力≈140", abs(tw["total_hashrate_th"]-140)<1)
ck("twpool worker=gpu10(去前缀)", [w["name"] for w in tw["workers"]]==["gpu10"])
ck("twpool 矿池余额=5", tw["pool_balance"]==5.0)
ck("twpool worker ip 为 None", tw["workers"][0]["ip"] is None)

mg=D.pool_view("merged")
ck("merged 两 worker 都在", {w["name"] for w in mg["workers"]}=={"wph","gpu10"})
ck("merged 总算力≈390", abs(mg["total_hashrate_th"]-390)<1)
ck("merged 矿池余额=15", mg["pool_balance"]==15.0)

# 单池 error 不崩, 另一池仍在
D.twpool_data=lambda force=False: {"_error":"boom"}
mg2=D.pool_view("merged")
ck("一池 error 合并仍含 wph", any(w["name"]=="wph" for w in mg2["workers"]))
ck("merged 记录 pool_error", bool(mg2.get("pool_error")))
# 未知 which → merged
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{f"{addr}.gpu10":{"hs":140000000000000}}}
ck("未知 which 回退 merged", {w["name"] for w in D.pool_view("zzz")["workers"]}=={"wph","gpu10"})

# hs 非数字不崩, 记 0
D.twpool_data=lambda force=False: {"balance":1.0,"paid":2.0,"reported":{f"{addr}.bad":{"hs":"NaNstr"}}}
twb=D.pool_view("twpool")
ck("hs 非数字 → 该 worker th=0 不崩", twb["workers"][0]["th"]==0.0)
# merged workers 按 name 排序
D.pool_data=lambda force=False: {"balance":10.0,"connected_workers":[{"worker_name":"zzz","ip":"1","gpu_info":[{"name":"x","hashrate":1000000000000}]}]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{f"{addr}.aaa":{"hs":2000000000000}}}
names=[w["name"] for w in D.pool_view("merged")["workers"]]
ck("merged workers 按 name 升序", names==sorted(names))
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
