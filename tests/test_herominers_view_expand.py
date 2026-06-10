#!/usr/bin/env python3
"""_herominers_view 扩展: shares{good,invalid,stale}=stats.shares_*; pool_info{network_height,blocks_found}。
运行: uv run python tests/test_herominers_view_expand.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"

D.herominers_data=lambda force=False: {
  "stats":{"balance":"5032239","hashrate":0,
           "shares_good":8,"shares_invalid":1,"shares_stale":2,
           "networkHeight":70811,"blocksFoundPool":3},
  "workers":[{"name":"w1","hashrate":0}], "payments":[]}
v=D._herominers_view()
ck("shares good=8/invalid=1/stale=2", v["shares"]=={"good":8,"invalid":1,"stale":2})
ck("pool_info.network_height=70811", v["pool_info"]["network_height"]==70811)
ck("pool_info.blocks_found=3", v["pool_info"]["blocks_found"]==3)
ck("pool_info.fee_rate=None", v["pool_info"]["fee_rate"] is None)
ck("余额仍=stats.balance 0.050322", abs(v["pool_balance"]-0.050322)<1e-6)

# 字段缺失 → shares 全 0 / pool_info 数值 None 不崩
D.herominers_data=lambda force=False: {"stats":{"balance":"0"},"workers":[],"payments":[]}
v2=D._herominers_view()
ck("无 shares 字段→全0", v2["shares"]=={"good":0,"invalid":0,"stale":0})
ck("无 networkHeight→None", v2["pool_info"]["network_height"] is None)

# Not-found → shares/pool_info None(非空记录)
D.herominers_data=lambda force=False: {"error":"Not found"}
v3=D._herominers_view()
ck("Not-found shares None", v3.get("shares") is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
