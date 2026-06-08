#!/usr/bin/env python3
"""migrate_account: runpod 原地 update 换镜像+完整env, vast DELETE 销毁; 单台失败不中断; config.pool 写入。
mock 所有网络函数, 不打网络。运行: python3 tests/test_migrate.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1

ADDR="prl1pTEST"; HOST="1.2.3.4:9000"
# --- runpod 账号迁移到 twpool ---
cfg={"prl_address":ADDR,"prl_host":HOST,"runpod":{"enabled":True},"vast":{"enabled":False}}
S.list_runpod_pods=lambda: [
    {"id":"podA","name":"wA","env":{"PRL_WORKER":"wA","PRL_HOST":HOST}},
    {"id":"podB","name":"wB","env":{"PRL_WORKER":"wB"}},
]
upd=[]
S.migrate_runpod_pod=lambda pid,image,env: upd.append((pid,image,dict(env)))
S.reset_low_eff_timers=lambda st: 0
r=S.migrate_account(cfg, {"rented":{}}, "runpod", "twpool", live=True)
ck("config.pool 写为 twpool", cfg.get("pool")=="twpool")
ck("两台 runpod 都发了 update", len(upd)==2)
ck("update 用 twpool 镜像", all(u[1]==S.POOLS["twpool"]["image"] for u in upd))
ck("env 含 PRL_ADDRESS/PRL_WORKER", all("PRL_ADDRESS" in u[2] and "PRL_WORKER" in u[2] for u in upd))
ck("twpool env 不含 PRL_HOST", all("PRL_HOST" not in u[2] for u in upd))
ck("worker 名沿用现有", {u[2]["PRL_WORKER"] for u in upd}=={"wA","wB"})
ck("结果含 2 条 runpod ok", sum(1 for x in r["results"] if x["platform"]=="runpod" and x["ok"])==2)

# --- pearlhash 目标: env 含 PRL_HOST ---
cfg2={"prl_address":ADDR,"prl_host":HOST,"runpod":{"enabled":True},"vast":{"enabled":False}}
upd.clear()
S.migrate_account(cfg2, {"rented":{}}, "runpod", "pearlhash", live=True)
ck("pearlhash env 含 PRL_HOST", upd and all(u[2].get("PRL_HOST")==HOST for u in upd))

# --- 单台失败不中断 ---
cfg3={"prl_address":ADDR,"prl_host":HOST,"runpod":{"enabled":True},"vast":{"enabled":False}}
S.list_runpod_pods=lambda:[{"id":"ok1","env":{"PRL_WORKER":"w1"}},{"id":"bad","env":{"PRL_WORKER":"w2"}},{"id":"ok2","env":{"PRL_WORKER":"w3"}}]
def upd_fail(pid,image,env):
    if pid=="bad": raise RuntimeError("boom")
S.migrate_runpod_pod=upd_fail
r3=S.migrate_account(cfg3,{"rented":{}},"runpod","twpool",live=True)
oks=[x for x in r3["results"] if x["platform"]=="runpod" and x["ok"]]
bads=[x for x in r3["results"] if x["platform"]=="runpod" and not x["ok"]]
ck("失败1台不中断: 2 ok 1 fail", len(oks)==2 and len(bads)==1)
ck("失败项带 error", bool(bads and bads[0].get("error")))

# --- vast 账号: DELETE 销毁 ---
cfgv={"prl_address":ADDR,"prl_host":HOST,"runpod":{"enabled":False},"vast":{"enabled":True}}
S.list_runpod_pods=lambda:[]
S.list_vast_instances=lambda:[{"id":111},{"id":222}]
deleted=[]
S.destroy_vast_instance=lambda iid: deleted.append(iid)
rv=S.migrate_account(cfgv,{"rented":{}},"vast","twpool",live=True)
ck("vast 两台都 DELETE", set(deleted)=={111,222})
ck("vast 结果 2 条 ok", sum(1 for x in rv["results"] if x["platform"]=="vast" and x["ok"])==2)

# --- live=False 不实际调用 ---
cfgd={"prl_address":ADDR,"prl_host":HOST,"runpod":{"enabled":True},"vast":{"enabled":False}}
S.list_runpod_pods=lambda:[{"id":"p1","env":{"PRL_WORKER":"w"}}]
called=[]
S.migrate_runpod_pod=lambda *a,**k: called.append(1)
S.migrate_account(cfgd,{"rented":{}},"runpod","twpool",live=False)
ck("live=False 不调 update", len(called)==0)

# --- 未知 pool 拒绝 ---
ck("未知 pool 返回 error", bool(S.migrate_account({"runpod":{"enabled":True}},{},"x","zzz",live=True).get("error")))

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
