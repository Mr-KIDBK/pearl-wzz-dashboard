#!/usr/bin/env python3
"""_pearlfortune_view 扩展: pending_balance(pending_estimate)/credited_total(ledger sum_credit)/
pool_paid 改 ledger sum_payout / workers[].stale / pool_info.fee_rate。
运行: uv run python tests/test_pearlfortune_view_expand.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.pearlfortune_pool_fee=lambda force=False: 0.05

# 完整: 待结算 + ledger 已付/收益 + 两个 worker(一 stale)
D.pearlfortune_data=lambda force=False: {
  "miner":{"data":{
    "balances":[{"balance_atomic":300000000}],
    "pending_shares":{"pending_estimate_amount_atomic":16017212},
    "credits":{"sum_amount_atomic":0}}},
  "connections":{"data":{"workers":[
    {"worker":"w1","reported_hashrate":144000000000000,"stale":False,"client_info":{"gpus":[{"model":"RTX 5070 Ti"}]}},
    {"worker":"w2","reported_hashrate":0,"stale":True}]}},
  "ledger":{"data":{"sum_payout_amount_atomic":"500000000","sum_credit_amount_atomic":"800000000"}},
}
v=D._pearlfortune_view()
ck("pool_balance=3.0(balances)", abs(v["pool_balance"]-3.0)<1e-6)
ck("pool_paid=5.0(ledger sum_payout)", abs(v["pool_paid"]-5.0)<1e-6)
ck("credited_total=8.0(ledger sum_credit)", abs(v["credited_total"]-8.0)<1e-6)
ck("pending_balance=0.16017212(pending_estimate)", abs(v["pending_balance"]-0.16017212)<1e-6)
ck("worker w1 非 stale / w2 stale", v["workers"][0]["stale"] is False and v["workers"][1]["stale"] is True)
ck("total_hashrate=144(w1)", abs(v["total_hashrate_th"]-144.0)<0.5)
ck("pool_info.fee_rate=0.05", v["pool_info"] and abs(v["pool_info"]["fee_rate"]-0.05)<1e-9)

# balances=null / ledger 缺失 → 0 不崩
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"pending_shares":{}}},"connections":{"data":{"workers":[]}}}
v2=D._pearlfortune_view()
ck("balances null→余额0", v2["pool_balance"]==0.0)
ck("ledger 缺失→已付0/收益0", v2["pool_paid"]==0.0 and v2["credited_total"]==0.0)
ck("pending 缺失→0", v2["pending_balance"]==0.0)
ck("无 worker→total 0", v2["total_hashrate_th"]==0.0)

# _error 透传
D.pearlfortune_data=lambda force=False: {"_error":"boom"}
ve=D._pearlfortune_view()
ck("_error 透传", ve["pool_error"]=="boom" and ve["pool_balance"] is None)

# 费率 None → pool_info=None
D.pearlfortune_pool_fee=lambda force=False: None
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
vn=D._pearlfortune_view()
ck("费率 None→pool_info None", vn["pool_info"] is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
