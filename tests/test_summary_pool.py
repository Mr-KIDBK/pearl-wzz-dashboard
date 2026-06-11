#!/usr/bin/env python3
"""build_summary(pool_key): 总算力/workers/产出 随视图; produced_basis 正确。
monkeypatch pool_view/tick_output/twpool_data/pool_data 不打网络。运行: python3 tests/test_summary_pool.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.pool_data=lambda force=False: {"balance":10.0,"connected_workers":[]}
D.twpool_data=lambda force=False: {"balance":5.0,"paid":20.0,"reported":{}}
D.herominers_data=lambda force=False: {"error":"Not found"}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"credits":{"sum_amount_atomic":0}}},"connections":{"data":{"workers":[]}}}
# build_summary 现在调 build_rentals; mock 返回空→running=0, running_by_pool 全零
D.build_rentals=lambda: {}
D.pool_view=lambda which: {
  "pearlhash":{"workers":[{"name":"wph","th":250.0,"ip":"1","gpus":[]}],"total_hashrate_th":250.0,"pool_balance":10.0,"pool_error":None},
  "twpool":{"workers":[{"name":"gpu10","th":140.0,"ip":None,"gpus":[]}],"total_hashrate_th":140.0,"pool_balance":5.0,"pool_error":None},
  "merged":{"workers":[{"name":"wph","th":250.0,"ip":"1","gpus":[]},{"name":"gpu10","th":140.0,"ip":None,"gpus":[]}],"total_hashrate_th":390.0,"pool_balance":15.0,"pool_error":None},
}.get(which, {"workers":[],"total_hashrate_th":0.0,"pool_balance":None,"pool_error":None})
D.tick_output=lambda pool=None: 100.0   # pearlhash 自重置产出固定 100
D.read_json=lambda p, default=None: {"reset_epoch": 0, "cumulative_usd": 0.0, "cumulative_usd_by_pool": {}}  # 干净 stats(无 baseline → sincere=alltime), 隔离真实 state
D.update_output_snapshot=lambda merged_out=None: None   # 隔离: 不写真实 STATS_PATH

s_ph=D.build_summary("pearlhash")
ck("pearlhash 总算力 250", s_ph["total_hashrate_th"]==250.0)
ck("pearlhash workers=wph", [w["name"] for w in s_ph["workers"]]==["wph"])
ck("pearlhash 产出=100(自重置)", s_ph["cumulative_output"]==100.0)
ck("pearlhash basis=since_reset", s_ph["produced_basis"]=="since_reset")
ck("pearlhash 矿池余额 10", s_ph["pool_balance"]==10.0)

s_tw=D.build_summary("twpool")
ck("twpool 总算力 140", s_tw["total_hashrate_th"]==140.0)
ck("twpool 产出=sincere(25-0)=25", s_tw["cumulative_output"]==25.0)
ck("twpool basis=since_reset", s_tw["produced_basis"]=="since_reset")
ck("twpool 矿池余额 5", s_tw["pool_balance"]==5.0)

s_mg=D.build_summary("merged")
ck("merged 总算力 390", s_mg["total_hashrate_th"]==390.0)
ck("merged 产出=ph100+sincere25=125", s_mg["cumulative_output"]==125.0)
ck("merged basis=since_reset", s_mg["produced_basis"]=="since_reset")
ck("merged 矿池余额 15", s_mg["pool_balance"]==15.0)

s_def=D.build_summary("zzz")   # 非法 → merged
ck("非法 pool 回退 merged(basis=since_reset)", s_def["produced_basis"]=="since_reset")

# 折合利润 = 产出*币价 - 累计租金, 随产出变(只断言结构存在)
ck("含 cumulative_profit_usd", "cumulative_profit_usd" in s_tw)
ck("含 pool_view 字段", s_tw.get("pool_view")=="twpool")
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
