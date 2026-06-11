#!/usr/bin/env python3
"""build_summary 产出口径: 待结算计入 alltime; avg_output_per_hour = 总产出 / 统计周期小时。
运行: uv run python tests/test_output_pending.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.coin_price=lambda: 1.0
D.build_rentals=lambda: {}
D.tick_output=lambda pool=None: 0.0     # pearlhash 产出 0, 隔离新池口径
D.update_output_snapshot=lambda merged_out=None: None   # 隔离: 不写真实 STATS_PATH
D.pearlfortune_pool_fee=lambda force=False: None
# stats: reset_epoch = 2 小时前
D.read_json=lambda p, default=None: {"reset_epoch": time.time()-7200, "cumulative_usd": 0.0, "cumulative_usd_by_pool": {}}
# pearlfortune: 余额0 + 待结算 0.16 → 累计产出应含 0.16
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"pending_shares":{"pending_estimate_amount_atomic":16000000}}},"connections":{"data":{"workers":[]}},"ledger":{"data":{"sum_payout_amount_atomic":"0","sum_credit_amount_atomic":"0"}}}
D.herominers_data=lambda force=False: {"error":"Not found"}
D.twpool_data=lambda force=False: {"balance":0.0,"paid":0.0,"reported":{}}
D.pool_data=lambda force=False: {"balance":0.0,"connected_workers":[]}

s=D.build_summary("pearlfortune")
ck("累计产出含待结算 0.16(balance0+paid0+pending0.16)", abs(s["cumulative_output"]-0.16)<1e-6)
ck("每小时产出=总产出/2h=0.08", abs(s["avg_output_per_hour"]-0.08)<1e-4)
ck("透传 pending_balance", abs((s.get("pending_balance") or 0)-0.16)<1e-6)

# reset 后: baseline = 当前 alltime(含pending 0.16)→ sincere=alltime-baseline=0 → 累计产出归零
D.read_json=lambda p, default=None: {"reset_epoch": time.time()-7200, "cumulative_usd":0.0, "cumulative_usd_by_pool":{}, "output_pearlfortune_baseline": 0.16}
ck("reset 后产出归零(baseline 含 pending=0.16)", abs(D.build_summary("pearlfortune")["cumulative_output"]-0.0)<1e-6)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
