#!/usr/bin/env python3
"""tick_spend 按池累计 cumulative_usd_by_pool(unknown 仅进总额); reset_stats 清零 per-pool。
运行: python3 tests/test_tick_spend_pool.py"""
import os, sys, json, time, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
tf=pathlib.Path(tempfile.mkdtemp())/"stats.json"
D.STATS_PATH=tf
D.build_rentals=lambda: {"acc":{"platform":"runpod","machines":[
    {"id":"a","price":0.3,"pool":"twpool"},{"id":"b","price":0.34,"pool":"pearlhash"},{"id":"c","price":0.5,"pool":"unknown"}]}}
D._is_running=lambda m: True
json.dump({"cumulative_usd":0.0,"cumulative_usd_by_pool":{},"last_epoch":time.time()-3600}, open(tf,"w"))
s=D.tick_spend()
bp=s.get("cumulative_usd_by_pool") or {}
ck("twpool 累计≈0.3", abs(bp.get("twpool",0)-0.3)<0.05)
ck("pearlhash 累计≈0.34", abs(bp.get("pearlhash",0)-0.34)<0.05)
ck("unknown 不进单池", bp.get("unknown",0)==0)
ck("总额含全部≈1.14", abs(s.get("cumulative_usd",0)-1.14)<0.1)
ck("current_hourly_by_pool twpool=0.3", (s.get("current_hourly_by_pool") or {}).get("twpool")==0.3)
ck("current_hourly_usd 总=1.14", abs(s.get("current_hourly_usd",0)-1.14)<0.01)
# reset 清零 per-pool
D.tick_output=lambda *a,**k: 0.0   # mock 掉网络调用
D.build_rentals=lambda: {}        # reset 时 tick_output 等不依赖; 避免真调
r=D.reset_stats()
s2=json.load(open(tf))
ck("reset 后 per-pool 清空", not s2.get("cumulative_usd_by_pool"))
ck("reset 后总额清零", s2.get("cumulative_usd")==0.0)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
