#!/usr/bin/env python3
"""salad 机器 image 透传: salad_live 实例带 image → build_rentals 该机器 pool 按镜像判定(非 unknown)。
运行: python3 tests/test_salad_pool_forward.py"""
import os, sys, time as _t
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.list_accounts=lambda: ["salad"]
D.platform_of=lambda a: "salad"
D.account_label=lambda a: a
D.read_config=lambda a: {"salad":{"enabled":True}}
D.rent_paused=lambda a: False
D.pid_for=lambda a: 1
D.platform_balance=lambda a, force=False: None
# salad_live 返回带 image 的实例(一台 twpool 镜像 一台 pearlhash 镜像)
D.salad_live=lambda a, force=False: {"instances":[
    {"id":"i1","gpu":"4090","price":0.2,"hashrate_th":140,"started_epoch":_t.time(),"state":"running","group":"gpu10","image":"docker.io/conishc/pearl-miner:twpool-v1.9.0-auto"},
    {"id":"i2","gpu":"4090","price":0.2,"hashrate_th":150,"started_epoch":_t.time(),"state":"running","group":"gpu1","image":"docker.io/mrkidbk/pearl-miner:latest"},
], "counts":{}, "error":None}
r=D.build_rentals()
by={m["id"]:m for m in r["salad"]["machines"]}
ck("salad 实例带 image 透传", all(m.get("image") for m in r["salad"]["machines"]))
ck("i1(conishc)→twpool", by["i1"].get("pool")=="twpool")
ck("i2(mrkidbk)→pearlhash", by["i2"].get("pool")=="pearlhash")
ck("salad 不再 unknown", all(m.get("pool")!="unknown" for m in r["salad"]["machines"]))
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
