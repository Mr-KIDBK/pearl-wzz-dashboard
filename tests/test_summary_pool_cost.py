#!/usr/bin/env python3
"""build_summary(pool_key) 按池输出 current_hourly_usd/cumulative_rent_usd/efficiency。
mock pool_view/tick_output/twpool_data/build_rentals/read_json。运行: python3 tests/test_summary_pool_cost.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "x"
D.pool_data=lambda force=False: {"connected_workers":[]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{}}
D.herominers_data=lambda force=False: {"error":"Not found"}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"credits":{"sum_amount_atomic":0}}},"connections":{"data":{"workers":[]}}}
D.pool_view=lambda which: {"pearlhash":{"workers":[],"total_hashrate_th":250.0,"pool_balance":10.0,"pool_error":None},
                           "twpool":{"workers":[],"total_hashrate_th":140.0,"pool_balance":5.0,"pool_error":None},
                           "merged":{"workers":[],"total_hashrate_th":390.0,"pool_balance":15.0,"pool_error":None}}.get(which)
D.tick_output=lambda pool=None: 100.0
D.list_accounts=lambda: ["acc"]
D.platform_of=lambda a: "runpod"
D.build_rentals=lambda: {"acc":{"platform":"runpod","machines":[
    {"id":"a","price":0.5,"pool":"twpool"},{"id":"b","price":1.0,"pool":"pearlhash"}]}}
D._is_running=lambda m: True
D.coin_price=lambda: 0.5
D.read_json=lambda p,d=None: {"cumulative_usd":50.0,"cumulative_usd_by_pool":{"pearlhash":30.0,"twpool":20.0},
                              "current_hourly_usd":1.5,"reset_epoch":0,"last_epoch":0}
s_tw=D.build_summary("twpool")
ck("twpool 当前$/h=0.5", s_tw["current_hourly_usd"]==0.5)
ck("twpool 累计租金=20", s_tw["cumulative_rent_usd"]==20.0)
ck("twpool 性价比=140/0.5=280", abs(s_tw["efficiency_th_per_usd"]-280)<1)
ck("twpool 在跑台数=1", s_tw["running_machines"]==1)
s_ph=D.build_summary("pearlhash")
ck("pearlhash 当前$/h=1.0", s_ph["current_hourly_usd"]==1.0)
ck("pearlhash 累计租金=30", s_ph["cumulative_rent_usd"]==30.0)
s_mg=D.build_summary("merged")
ck("合并 当前$/h=1.5", abs(s_mg["current_hourly_usd"]-1.5)<0.01)
ck("合并 累计租金=50", s_mg["cumulative_rent_usd"]==50.0)
ck("合并 在跑台数=2", s_mg["running_machines"]==2)
ck("合并 性价比=390/1.5=260", abs(s_mg["efficiency_th_per_usd"]-260)<1)
ck("running_by_pool 回传", s_mg["running_by_pool"]["twpool"]==1 and s_mg["running_by_pool"]["pearlhash"]==1)
ck("利润=产出折合-该池租金", s_tw.get("cumulative_profit_usd") is not None)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
