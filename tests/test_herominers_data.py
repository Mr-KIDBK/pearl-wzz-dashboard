#!/usr/bin/env python3
"""herominers_data/_herominers_view: 解析 stats.balance/paid(原子1e8)+ workers; Not-found 视为空非错。
monkeypatch herominers_data 不打网络。运行: python3 tests/test_herominers_data.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"

# 正常: stats.balance/paid 原子单位 1e8; workers 防御性(逐-worker 真实结构待迁移测试定型)
D.herominers_data=lambda force=False: {
    "stats": {"balance": "150000000", "paid": "900000000", "hashrate": 0},
    "workers": {},
}
v = D._herominers_view()
ck("herominers 余额=1.5(150000000/1e8)", abs(v["pool_balance"]-1.5) < 1e-6)
ck("herominers 已付=9.0(900000000/1e8)", abs(v["pool_paid"]-9.0) < 1e-6)
ck("herominers 无 error", v["pool_error"] is None)
ck("herominers workers 是列表", isinstance(v["workers"], list))
ck("herominers total_hashrate_th 是数字", isinstance(v["total_hashrate_th"], (int,float)))

# Not-found(我们钱包尚未在此挖)→ 视为空, 余额0, 非错误
D.herominers_data=lambda force=False: {"error": "Not found"}
v2 = D._herominers_view()
ck("Not-found 余额=0", v2["pool_balance"] == 0.0)
ck("Not-found 非错误(pool_error None)", v2["pool_error"] is None)
ck("Not-found 无 worker", v2["workers"] == [])

# 网络失败 _error → pool_error 传出
D.herominers_data=lambda force=False: {"_error": "URLError: boom"}
v3 = D._herominers_view()
ck("_error 传出 pool_error", bool(v3["pool_error"]))

# workers 为 list[dict] 也能解析
D.herominers_data=lambda force=False: {"stats":{"balance":"0","paid":"0"},"workers":[{"name":"w1","hashrate":140000000000000}]}
v4=D._herominers_view()
ck("list workers 解析 w1≈140TH", bool(v4["workers"]) and v4["workers"][0]["name"]=="w1" and abs(v4["workers"][0]["th"]-140)<1)
# workers 为 list 含非 dict 元素不崩
D.herominers_data=lambda force=False: {"stats":{"balance":"0","paid":"0"},"workers":["junk",{"name":"w2","hashrate":0}]}
v5=D._herominers_view()
ck("list 含非dict元素不崩", isinstance(v5["workers"], list))

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
