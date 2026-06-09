#!/usr/bin/env python3
"""build_full_config 的 pools 每项含 id/label/image/reads_prl_host。
运行: python3 tests/test_full_config_pools.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
full=D.build_full_config()
pools=full.get("pools") or []
ck("pools 非空(≥2)", len(pools)>=2)
ck("每项含 id/label/image/reads_prl_host", all(all(k in p for k in ("id","label","image","reads_prl_host")) for p in pools))
by={p["id"]:p for p in pools}
ck("pearlhash image 含 kuzigmgm", "kuzigmgm" in (by.get("pearlhash",{}).get("image") or ""))
ck("pearlhash reads_prl_host=True", by.get("pearlhash",{}).get("reads_prl_host") is True)
ck("twpool image 含 mrkidbk/pearl-miner-twpool", "mrkidbk/pearl-miner-twpool" in (by.get("twpool",{}).get("image") or ""))
ck("twpool reads_prl_host=False", by.get("twpool",{}).get("reads_prl_host") is False)
pool_ids = {p["id"] for p in full["pools"]}
ck("full_config pools 含 4 池", {"pearlhash","twpool","herominers","pearlfortune"} <= pool_ids)
hm = next(p for p in full["pools"] if p["id"]=="herominers")
ck("herominers label/reads_prl_host 正确", hm["label"]=="HeroMiners" and hm["reads_prl_host"] is False)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
