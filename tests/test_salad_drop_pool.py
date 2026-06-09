#!/usr/bin/env python3
"""tick_spend: salad 真实余额下降归到该账号机器实际所在池(非固定 twpool)。
monkeypatch STATS/build_rentals/list_accounts/platform_of/salad_real_balance。运行: python3 tests/test_salad_drop_pool.py"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False); tf.close()
D.STATS_PATH = tf.name
# salad 账号 sd, 机器都在 herominers 池
D.build_rentals=lambda: {"sd": {"platform":"salad","machines":[{"price":0.1,"pool":"herominers","state":"running"}]}}
D.list_accounts=lambda: ["sd"]
D.platform_of=lambda a: "salad"
# 第一次: 余额 10(仅记 prev, 无 drop)
seq = {"sd": 10.0}
D.salad_real_balance=lambda a: seq["sd"]
json.dump({"cumulative_usd":0.0,"cumulative_usd_by_pool":{},"last_epoch":__import__("time").time()}, open(tf.name,"w"))
D.tick_spend()
# 第二次: 余额降到 7 → drop=3 → 应进 herominers 桶(非 twpool)
seq["sd"] = 7.0
s = D.tick_spend()
cbp = s.get("cumulative_usd_by_pool") or {}
ck("salad drop=3 进 herominers 桶", abs(cbp.get("herominers",0.0)-3.0) < 1e-6)
ck("twpool 桶未被误加", abs(cbp.get("twpool",0.0)-0.0) < 1e-6)
ck("cumulative_usd 总额+3", abs(s.get("cumulative_usd",0.0)-3.0) < 1e-6)
os.unlink(tf.name)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
