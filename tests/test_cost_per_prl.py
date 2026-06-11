#!/usr/bin/env python3
"""build_summary.cost_per_prl_usd: 现在挖 1 PRL 的 USD 成本 = cur_hourly / avg_output_per_hour。
运行: uv run python tests/test_cost_per_prl.py"""
import os, sys, time as _t
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.coin_price=lambda: 1.0
D.tick_output=lambda pool=None: 0.0
D.update_output_snapshot=lambda merged_out=None: None   # 隔离: 不写真实 STATS_PATH(Task3 改为控制 recent3h)
D.read_json=lambda p, default=None: {"reset_epoch": _t.time()-7200, "cumulative_usd":0.0, "cumulative_usd_by_pool":{}}
D.pearlfortune_pool_fee=lambda force=False: None
D.herominers_data=lambda force=False: {"error":"Not found"}
D.twpool_data=lambda force=False: {"balance":0,"paid":0,"reported":{}}
D.pool_data=lambda force=False: {"balance":0,"connected_workers":[]}
# 1 台 pearlfortune 机器 $2/h; pending 0.32 PRL → output=0.32, reset 2h前 → avg=0.16/h → cost=2/0.16=12.5
D.build_rentals=lambda: {"runpod":{"platform":"runpod","machines":[{"price":2.0,"pool":"pearlfortune"}]}}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"pending_shares":{"pending_estimate_amount_atomic":32000000}}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
s=D.build_summary("pearlfortune")
ck("cur_hourly=2.0", abs(s["current_hourly_usd"]-2.0)<1e-6)
ck("avg_output_per_hour=0.16", abs(s["avg_output_per_hour"]-0.16)<1e-3)
ck("cost_per_prl_usd=12.5(2.0/0.16)", abs(s["cost_per_prl_usd"]-12.5)<0.05)

# 无产出(avg=0)→ cost None 不崩
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
s2=D.build_summary("pearlfortune")
ck("无产出 → cost None", s2["cost_per_prl_usd"] is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
