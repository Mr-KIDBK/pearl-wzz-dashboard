#!/usr/bin/env python3
"""build_summary.avg_output_per_hour: 总产出(含 pending)/ 统计周期小时(now - reset_epoch)。
不再扣 baseline 自重置增量; 随 pool_key; hours>0 守卫。
tick_output 仍惰性写 output_<pool>_baseline(reset 语义保留, 但 avg 不再用)。
运行: python3 tests/test_avg_output.py"""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
REAL_TICK_OUTPUT = D.tick_output  # 真实 tick_output(下方会 mock 掉, 留引用做 baseline 测试)

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

class FakeTime:
    t = 1_000_000.0
    @staticmethod
    def time(): return FakeTime.t
D.time = FakeTime

tmp = Path(tempfile.mkdtemp()); D.STATS_PATH = tmp / "stats.json"
D.coin_price = lambda: 0.5
D.tick_output = lambda pool=None: 2.0                  # ph_output 自重置=2.0
D.pool_data = lambda force=False: {}
D.twpool_data = lambda force=False: {"balance": 50.0, "paid": 50.0}  # tw_total=100
D.herominers_data=lambda force=False: {"error":"Not found"}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
D.pearlfortune_pool_fee=lambda force=False: None       # 不打网络
D.build_rentals = lambda: {}                           # 无机器 → burn 0
D.pool_view = lambda which: {"total_hashrate_th": 0.0, "workers": [], "pool_balance": None, "pool_error": None}
D.prl_address = lambda: "prl1x"

# reset 2h 前; 新口径 avg = 总产出 / 2h(不扣 baseline; baseline 留作 reset 语义)
json.dump({"reset_epoch": FakeTime.t - 7200, "output_tw_baseline": 90.0,
           "cumulative_usd": 0.0, "cumulative_usd_by_pool": {}}, open(D.STATS_PATH, "w"))

ph = D.build_summary("pearlhash")
tw = D.build_summary("twpool")
mg = D.build_summary("merged")
ck("pearlhash avg = ph_output(2)/2h = 1.0", abs(ph["avg_output_per_hour"] - 1.0) < 1e-6)
ck("twpool avg = sincere(100-90=10)/2h = 5.0", abs(tw["avg_output_per_hour"] - 5.0) < 1e-6)
ck("merged avg = (ph2 + sincere10)/2h = 6.0", abs(mg["avg_output_per_hour"] - 6.0) < 1e-6)

# 刚重置(hours=0)→ None
json.dump({"reset_epoch": FakeTime.t, "output_tw_baseline": 90.0}, open(D.STATS_PATH, "w"))
ck("hours<=0 → avg None", D.build_summary("merged")["avg_output_per_hour"] is None)

# 自重置口径: tot<baseline(提现后)→ sincere=max(0,100-150)=0 → avg=0
json.dump({"reset_epoch": FakeTime.t - 3600, "output_tw_baseline": 150.0}, open(D.STATS_PATH, "w"))
ck("tot<baseline → sincere=0 → twpool avg=0", D.build_summary("twpool")["avg_output_per_hour"] == 0.0)

# tick_output 惰性基线: twpool error-dict → 不设 baseline(留待重试, 避免基线=0); 正常数据 → 设
PEARL_POOL = {"pending_rewards": {"total_pending": 0}, "balance_transactions": []}
D.twpool_data = lambda force=False: {"_error": "timeout"}
json.dump({}, open(D.STATS_PATH, "w"))
REAL_TICK_OUTPUT(PEARL_POOL)
ck("twpool error → 不设 output_tw_baseline(留待重试)", "output_tw_baseline" not in json.load(open(D.STATS_PATH)))
D.twpool_data = lambda force=False: {"balance": 30.0, "paid": 70.0}
REAL_TICK_OUTPUT(PEARL_POOL)
ck("twpool 正常 → 设 baseline=100.0", json.load(open(D.STATS_PATH)).get("output_tw_baseline") == 100.0)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
