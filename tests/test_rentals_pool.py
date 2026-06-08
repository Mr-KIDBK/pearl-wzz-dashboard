#!/usr/bin/env python3
"""build_rentals 每台机器含 pool(镜像优先)。
mock 依赖不打网络。运行: python3 tests/test_rentals_pool.py"""
import os, sys, time as _t
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.list_accounts=lambda: ["runpod"]
D.platform_of=lambda a: "runpod"
D.account_label=lambda a: a
D.read_config=lambda a: {"runpod":{"enabled":True}}
D.rent_paused=lambda a: False
D.pid_for=lambda a: 1
D.platform_balance=lambda a, force=False: None
D.account_machine_images=lambda a, force=False: {"podA":"docker.io/conishc/x:twpool","podB":"docker.io/kuzigmgm/y:v11"}
D.active_rentals=lambda a: [
    {"id":"podA","gpu":"4090","price":0.3,"hashrate_th":140,"created_epoch":_t.time(),"worker":"wA"},
    {"id":"podB","gpu":"4090","price":0.34,"hashrate_th":250,"created_epoch":_t.time(),"worker":"wB"},
]
r=D.build_rentals()
by={m["id"]:m for m in r["runpod"]["machines"]}
ck("每台机器有 pool 字段", all("pool" in m for m in r["runpod"]["machines"]))
ck("podA pool=twpool(镜像)", by["podA"].get("pool")=="twpool")
ck("podB pool=pearlhash(镜像)", by["podB"].get("pool")=="pearlhash")
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
