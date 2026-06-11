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

# --- twpool: history 多 worker 按 time 合并求和(真实 H/s → TH) ---
D.twpool_data=lambda force=False: {"reported":{},"balance":0,"paid":0,"history":{
  "addr.w1":[{"time":1781037480,"hashrate":249332312823027},{"time":1781037600,"hashrate":218144801430118}],
  "addr.w2":[{"time":1781037480,"hashrate":100000000000000}],
}}
ts=D._twpool_view()["hashrate_series"]
ck("twpool unit=TH", ts and ts["unit"]=="TH")
ck("twpool t1 合并 w1+w2=(249.33+100)≈349.33TH", abs(ts["points"][0][1]-349.33)<0.1)
ck("twpool t2 仅 w1≈218.14TH", abs(ts["points"][1][1]-218.14)<0.1)
ck("twpool 点按 ts 升序", ts["points"][0][0] < ts["points"][1][0])
D.twpool_data=lambda force=False: {"reported":{},"balance":0,"paid":0}
ck("twpool 无 history → None", D._twpool_view()["hashrate_series"] is None)
D.twpool_data=lambda force=False: {"_error":"boom"}
ck("twpool error → None", D._twpool_view()["hashrate_series"] is None)

# --- herominers: charts.hashrate [ts,hr,workers] → points(share 刻度)---
D.herominers_data=lambda force=False: {"stats":{"balance":"0"},"workers":[],"payments":[],"charts":{"hashrate":[[1781023455,35476,4],[1781024175,2381,4]]}}
hh=D._herominers_view()["hashrate_series"]
ck("hm unit=share", hh and hh["unit"]=="share")
ck("hm 点数=2 + 值原样", len(hh["points"])==2 and hh["points"][0][1]==35476)
ck("hm 点按 ts 升序", hh["points"][0][0] < hh["points"][1][0])
D.herominers_data=lambda force=False: {"stats":{"balance":"0"},"workers":[],"payments":[]}
ck("hm 无 charts → None", D._herominers_view()["hashrate_series"] is None)
D.herominers_data=lambda force=False: {"error":"Not found"}
ck("hm Not-found → None", D._herominers_view().get("hashrate_series") is None)

# --- pool_view 单池透传 / merged None;build_summary 透传 ---
D.pool_data=lambda force=False: {"balance":0,"connected_workers":[]}
D.twpool_data=lambda force=False: {"reported":{},"balance":0,"paid":0,"history":{"a.w1":[{"time":1781037480,"hashrate":100000000000000}]}}
D.herominers_data=lambda force=False: {"error":"Not found"}
D.pearlfortune_data=lambda force=False: {"miner":{"data":{"balances":None}},"connections":{"data":{"workers":[]}},"ledger":{"data":{}}}
ck("pool_view twpool 透传 series", (D.pool_view("twpool").get("hashrate_series") or {}).get("unit")=="TH")
ck("pool_view pearlhash → None(无字段)", D.pool_view("pearlhash").get("hashrate_series") is None)
ck("pool_view merged → None", D.pool_view("merged").get("hashrate_series") is None)
# build_summary 透传
D.coin_price=lambda: 1.0; D.build_rentals=lambda: {}; D.tick_output=lambda pool=None: 0.0
D.update_output_snapshot=lambda merged_out=None: None   # 隔离: 不写真实 STATS_PATH
import time as _t
D.read_json=lambda p, default=None: {"reset_epoch": _t.time()-3600, "cumulative_usd":0.0, "cumulative_usd_by_pool":{}}
ck("build_summary(twpool) 透传 hashrate_series", (D.build_summary("twpool").get("hashrate_series") or {}).get("unit")=="TH")
ck("build_summary(merged) hashrate_series None", D.build_summary("merged").get("hashrate_series") is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
