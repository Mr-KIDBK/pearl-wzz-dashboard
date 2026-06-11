#!/usr/bin/env python3
"""build_summary 两成本: cost_cumulative_usd(累计租金/产出, 视图) + cost_recent3h_usd(burn×3/最近3h产出, 全局)。
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
D.pearlfortune_pool_fee=lambda force=False: None
D.herominers_data=lambda force=False: {"error":"Not found"}
D.twpool_data=lambda force=False: {"balance":0,"paid":0,"reported":{}}
D.pool_data=lambda force=False: {"balance":0,"connected_workers":[]}
# 1 台 pearlfortune $2/h; pending 0.32 PRL → output(sincere, baseline缺=0)=0.32
D.build_rentals=lambda: {"runpod":{"platform":"runpod","machines":[{"price":2.0,"pool":"pearlfortune"}]}}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"pending_shares":{"pending_estimate_amount_atomic":32000000}}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}

# 累计成本: rent(cbp[pearlfortune]=5.0)/output(0.32)=15.625; 最近3h: burn(2.0)×3/recent3h(0.6)=10.0
D.read_json=lambda p, default=None: {"reset_epoch": _t.time()-7200, "cumulative_usd":0.0, "cumulative_usd_by_pool":{"pearlfortune":5.0}}
D.update_output_snapshot=lambda merged_out=None: 0.6   # 控制 recent3h_output=0.6(也隔离不写真实文件)
s=D.build_summary("pearlfortune")
ck("有两成本字段且无旧 cost_per_prl_usd",
   "cost_cumulative_usd" in s and "cost_recent3h_usd" in s and "cost_per_prl_usd" not in s)
ck("累计成本 = rent5/output0.32 ≈ 15.625", abs(s["cost_cumulative_usd"]-15.625)<0.1)
ck("最近3h成本 = burn2×3 / 0.6 = 10.0", abs(s["cost_recent3h_usd"]-10.0)<0.05)

# recent3h_output None → cost_recent3h None
D.update_output_snapshot=lambda merged_out=None: None
ck("最近3h无数据 → None", D.build_summary("pearlfortune")["cost_recent3h_usd"] is None)

# 无产出(output=0)→ 累计成本 None
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
ck("无产出 → 累计成本 None", D.build_summary("pearlfortune")["cost_cumulative_usd"] is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
