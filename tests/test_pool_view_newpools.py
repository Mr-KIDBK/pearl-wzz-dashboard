#!/usr/bin/env python3
"""pool_view 含 herominers/pearlfortune; merged 跨 4 池合并(余额/已付/算力)。
monkeypatch 各池 *_data。运行: python3 tests/test_pool_view_newpools.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
addr="prl1pX"; D.prl_address=lambda: addr
D.pool_data=lambda force=False: {"balance":10.0,"connected_workers":[]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{}}
D.herominers_data=lambda force=False: {"stats":{"balance":"100000000","hashrate":0},"workers":[],"unconfirmed":[],"unlocked":[],"payments":[]}  # 1.0
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":[{"balance_atomic":200000000}],"credits":{"sum_amount_atomic":0}}},"connections":{"data":{"workers":[]}}}  # 2.0

ck("pool_view('herominers') 余额=1.0", abs(D.pool_view("herominers")["pool_balance"]-1.0) < 1e-6)
ck("pool_view('pearlfortune') 余额=2.0", abs(D.pool_view("pearlfortune")["pool_balance"]-2.0) < 1e-6)
mg = D.pool_view("merged")
ck("merged 余额=10+5+1+2=18", abs(mg["pool_balance"]-18.0) < 1e-6)
ck("merged 不崩, workers 是列表", isinstance(mg["workers"], list))

# 某新池 error → merged 仍含其它池余额(此处其它池余额 17)
D.herominers_data=lambda force=False: {"_error":"boom"}
mg2 = D.pool_view("merged")
ck("一新池 error → merged 记 pool_error", bool(mg2.get("pool_error")))
ck("一新池 error → merged 余额=10+5+2=17(跳过 error 池)", abs(mg2["pool_balance"]-17.0) < 1e-6)

# pool_paid 覆盖: twpool=data.paid, pearlhash=None, 新池空=0, merged 求和(跳过 None)
D.pool_data=lambda force=False: {"balance":10.0,"connected_workers":[]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{}}
D.herominers_data=lambda force=False: {"stats":{"balance":"100000000","hashrate":0},"workers":[],"unconfirmed":[],"unlocked":[],"payments":[]}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"credits":{"sum_amount_atomic":0}}},"connections":{"data":{"workers":[]}}}
ck("twpool pool_paid=20", abs(D.pool_view("twpool")["pool_paid"]-20.0) < 1e-6)
ck("pearlhash pool_paid=None", D.pool_view("pearlhash")["pool_paid"] is None)
ck("herominers pool_paid=0", D.pool_view("herominers")["pool_paid"]==0.0)
ck("merged pool_paid=20(twpool20+新池0, pearlhash None 跳过)", abs(D.pool_view("merged")["pool_paid"]-20.0) < 1e-6)

# 实测真实响应: herominers 余额在 stats.balance(字符串原子/1e8), 非 unconfirmed/unlocked
# (unconfirmed=[]; unlocked 是冒号分隔的区块明细串, 不是金额列表)
D.herominers_data=lambda force=False: {
    "stats":{"balance":"5032239","hashrate":0,"hashrate_24h":533.19},
    "workers":[{"name":"rp2-rtx-4090","hashrate":0}],
    "unconfirmed":[],
    "unlocked":["70502:hashx:1081831884909:262895797484:5032239:23714286:1246244858188:unlocked:hashx:as-sg:prop:1246244858188:23714286:1238889019121","1781024776"],
    "unlocked_daily":["5032239","0"],
    "payments":[]}
ck("herominers 余额读 stats.balance=0.050322", abs(D.pool_view("herominers")["pool_balance"]-0.050322) < 1e-6)
ck("herominers payments 空 → pool_paid=0", D.pool_view("herominers")["pool_paid"]==0.0)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
