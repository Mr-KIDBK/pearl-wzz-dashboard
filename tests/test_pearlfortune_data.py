#!/usr/bin/env python3
"""pearlfortune_data/_pearlfortune_view: 解析 balances.balance_atomic(余额) / ledger sum_payout_amount_atomic(已付)
/ sum_credit_amount_atomic(累计收益) / pending_estimate(待结算) + connections.workers。原子1e8。
monkeypatch 不打网络。运行: python3 tests/test_pearlfortune_data.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.pearlfortune_pool_fee=lambda force=False: None   # 不打网络

# 正常: balances 列表(余额) + ledger(已付/收益) + pending_shares(待结算) + connections.workers
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {
        "balances": [{"balance_atomic": 250000000}],          # 2.5 PRL
        "pending_shares": {"pending_estimate_amount_atomic": 50000000},   # 待结算 0.5 PRL
    }},
    "connections": {"data": {"configured": True, "online": True, "workers": []}},
    "ledger": {"data": {"sum_payout_amount_atomic": "1000000000", "sum_credit_amount_atomic": "1500000000"}},  # 已付10 / 累计收益15
}
v = D._pearlfortune_view()
ck("pf 余额=2.5(250000000/1e8)", abs(v["pool_balance"]-2.5) < 1e-6)
ck("pf 已付=10.0(ledger sum_payout)", abs(v["pool_paid"]-10.0) < 1e-6)
ck("pf 累计收益=15.0(ledger sum_credit)", abs(v["credited_total"]-15.0) < 1e-6)
ck("pf 待结算=0.5(pending_estimate)", abs(v["pending_balance"]-0.5) < 1e-6)
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

# connections.workers list[dict] 解析 + 含非 dict 元素不崩(真实字段 reported_hashrate)
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": [{"worker":"w1","reported_hashrate":140000000000000}, "junk"]}},
}
vw = D._pearlfortune_view()
ck("pf list worker 解析 w1≈140TH", bool(vw["workers"]) and vw["workers"][0]["name"]=="w1" and abs(vw["workers"][0]["th"]-140)<1)
ck("pf workers 含非dict元素不崩", isinstance(vw["workers"], list))

# 真实 connections worker 结构(reported_hashrate, worker 名 'worker' 字段)
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": [{"worker":"rp2-x-4090","reported_hashrate":269530616888726.12,"stale":False,"client_info":{"gpus":[{"model":"NVIDIA GeForce RTX 4090"}]}}]}},
}
vr = D._pearlfortune_view()
ck("pf reported_hashrate→269.5TH", bool(vr["workers"]) and abs(vr["workers"][0]["th"]-269.53) < 0.1)
ck("pf worker 名取 worker 字段", vr["workers"][0]["name"]=="rp2-x-4090")
ck("pf gpu 取 client_info", vr["workers"][0]["gpus"]==["NVIDIA GeForce RTX 4090"])

# 网络失败 → pool_error 传出
D.pearlfortune_data=lambda force=False: {"_error": "URLError: boom"}
ck("_error 传出 pool_error", bool(D._pearlfortune_view()["pool_error"]))

# reported_hashrate=0 的 worker → th=0(不因 falsy 而误读)
D.pearlfortune_data=lambda force=False: {
    "miner": {"data": {"balances": None, "credits": {"sum_amount_atomic": 0}}},
    "connections": {"data": {"workers": [{"worker":"idle","reported_hashrate":0}]}},
}
v0 = D._pearlfortune_view()
ck("reported_hashrate=0 → th=0", bool(v0["workers"]) and v0["workers"][0]["th"]==0.0)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
