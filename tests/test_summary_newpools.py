#!/usr/bin/env python3
"""build_summary 支持 herominers/pearlfortune pool_key; rbp/bbp/cbp 含新池键; merged 含新池 output。
monkeypatch build_rentals/各池 data/coin_price/STATS。运行: python3 tests/test_summary_newpools.py"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
addr="prl1pX"; D.prl_address=lambda: addr
D.coin_price=lambda: 1.0
tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False); tf.close()
D.STATS_PATH = tf.name
# 预设各池 baseline=0 → tick_output 不覆盖(if in s skip)→ sincere=alltime(测聚合, 不被"首次 baseline=alltime 归零"干扰)
json.dump({"cumulative_usd":0.0,"cumulative_usd_by_pool":{"herominers":3.0,"pearlfortune":4.0},"reset_epoch":0,
           "output_herominers_baseline":0.0,"output_pearlfortune_baseline":0.0,"output_tw_baseline":0.0}, open(tf.name,"w"))
# 池数据
D.pool_data=lambda force=False: {"balance":0.0,"connected_workers":[],"pending_rewards":{"total_pending":0},"balance_transactions":[]}
D.twpool_data=lambda force=False: {"balance":0.0,"paid":0.0,"reported":{}}
D.herominers_data=lambda force=False: {"stats":{"balance":"100000000","hashrate":0},"workers":[],"unconfirmed":[],"unlocked":[],"payments":[{"amount":500000000}]}  # 余额1(stats.balance)+已付5(payments)=6 all-time
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":[{"balance_atomic":200000000}]}},"connections":{"data":{"workers":[]}},"ledger":{"data":{"sum_payout_amount_atomic":"800000000"}}}  # 余额2 + ledger payout8 = 10
# 机器: 1 台 herominers, 1 台 pearlfortune
D.build_rentals=lambda: {
  "rp": {"platform":"runpod","machines":[{"price":0.3,"pool":"herominers","state":None}]},
  "pf": {"platform":"runpod","machines":[{"price":0.4,"pool":"pearlfortune","state":None}]},
}
D.list_accounts=lambda: ["rp","pf"]
D.platform_of=lambda a: "runpod"

s_hm = D.build_summary("herominers")
ck("herominers pool_view 选中", s_hm["pool_view"]=="herominers")
ck("herominers running=1", s_hm["running_machines"]==1)
ck("herominers cumulative_output=6(balance+paid)", abs(s_hm["cumulative_output"]-6.0) < 1e-6)
ck("herominers 当前$/h=0.3", abs(s_hm["current_hourly_usd"]-0.3) < 1e-6)
ck("herominers 累计租金=cbp[herominers]=3.0", abs(s_hm["cumulative_rent_usd"]-3.0) < 1e-6)

s_pf = D.build_summary("pearlfortune")
ck("pearlfortune cumulative_output=10", abs(s_pf["cumulative_output"]-10.0) < 1e-6)
ck("pearlfortune 累计租金=4.0", abs(s_pf["cumulative_rent_usd"]-4.0) < 1e-6)

s_mg = D.build_summary("merged")
ck("merged running=2", s_mg["running_machines"]==2)
ck("merged running_by_pool 含新池键", {"herominers","pearlfortune"} <= set(s_mg["running_by_pool"].keys()))
ck("merged cumulative_output=ph0+tw0+6+10=16", abs(s_mg["cumulative_output"]-16.0) < 1e-6)
ck("summary 含 pools 列表(4 池)", {p["id"] for p in s_mg.get("pools",[])} == {"pearlhash","twpool","herominers","pearlfortune"})
os.unlink(tf.name)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
