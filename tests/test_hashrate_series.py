#!/usr/bin/env python3
"""三池 hashrate_series: pf(series 推算 TH)/twpool(history 合并 TH)/hm(charts share)。
运行: uv run python tests/test_hashrate_series.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"
D.pearlfortune_pool_fee=lambda force=False: None

# --- pearlfortune: series 推算 share_sum/total*pool_hashrate ---
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None,"hourly_shares":{"series":[
  {"hour":1781060400,"share_sum":0,"total_share_sum":52504,"pool_hashrate":6894123500000000000},
  {"hour":1781067600,"share_sum":1,"total_share_sum":30360,"pool_hashrate":3995098400000000000},
]}}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
hs=D._pearlfortune_view()["hashrate_series"]
ck("pf unit=TH", hs and hs["unit"]=="TH")
ck("pf 第1点(share0)→0", abs(hs["points"][0][1]-0.0)<1e-9)
ck("pf 第2点(share1)→131.59TH", abs(hs["points"][1][1]-131.59)<0.1)
ck("pf 点按 ts 升序", hs["points"][0][0] < hs["points"][1][0])
# total_share_sum=0 不除零崩
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"hourly_shares":{"series":[{"hour":1,"share_sum":5,"total_share_sum":0,"pool_hashrate":1e18}]}}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
ck("pf total=0 不崩→0", D._pearlfortune_view()["hashrate_series"]["points"][0][1]==0.0)
# 无 series → None
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
ck("pf 无 series → None", D._pearlfortune_view()["hashrate_series"] is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
