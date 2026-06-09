#!/usr/bin/env python3
"""pearlfortune_data/_pearlfortune_view: 解析 balances.balance_atomic / credits.sum_amount_atomic(原子1e8)
+ connections.workers。monkeypatch 不打网络。运行: python3 tests/test_pearlfortune_data.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"

# 正常: balances 列表(含 balance_atomic) + credits.sum_amount_atomic + connections.workers
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {
        "balances": [{"balance_atomic": 250000000}],          # 2.5 PRL
        "credits": {"sum_amount_atomic": 1000000000},         # 10 PRL 已结算
        "pending_shares": {"pending_estimate_amount_atomic": 50000000},
    }},
    "connections": {"data": {"configured": True, "online": True, "workers": []}},
}
v = D._pearlfortune_view()
ck("pf 余额=2.5(250000000/1e8)", abs(v["pool_balance"]-2.5) < 1e-6)
ck("pf 已付=10.0(sum_amount_atomic/1e8)", abs(v["pool_paid"]-10.0) < 1e-6)
ck("pf 无 error", v["pool_error"] is None)
ck("pf workers 是列表", isinstance(v["workers"], list))

# balances null(尚未挖)→ 余额0 非错误
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}, "pending_shares": {"pending_estimate_amount_atomic": 0}}},
    "connections": {"data": {"configured": True, "online": False, "workers": []}},
}
v2 = D._pearlfortune_view()
ck("balances null → 余额0", v2["pool_balance"] == 0.0)
ck("balances null → 非错误", v2["pool_error"] is None)

# balances 单对象(非列表)也能解析
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": {"balance_atomic": 300000000}, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": []}},
}
ck("balances 单对象 → 余额3.0", abs(D._pearlfortune_view()["pool_balance"]-3.0) < 1e-6)

# connections.workers list[dict] 解析 + 含非 dict 元素不崩
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": [{"name":"w1","hashrate":140000000000000}, "junk"]}},
}
vw = D._pearlfortune_view()
ck("pf list worker 解析 w1≈140TH", bool(vw["workers"]) and vw["workers"][0]["name"]=="w1" and abs(vw["workers"][0]["th"]-140)<1)
ck("pf workers 含非dict元素不崩", isinstance(vw["workers"], list))

# 网络失败 → pool_error 传出
D.pearlfortune_data=lambda force=False: {"_error": "URLError: boom"}
ck("_error 传出 pool_error", bool(D._pearlfortune_view()["pool_error"]))

# hashrate=0 的 worker → th=0(不因 falsy 而误读)
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": [{"name":"idle","hashrate":0}]}},
}
v0 = D._pearlfortune_view()
ck("hashrate=0 → th=0", bool(v0["workers"]) and v0["workers"][0]["th"]==0.0)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
