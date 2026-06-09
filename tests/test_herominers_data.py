#!/usr/bin/env python3
"""herominers_data/_herominers_view: 余额=顶层 unconfirmed+unlocked, 已付=payments(原子1e8, 元素防御性)
+ workers; Not-found 视为空非错。monkeypatch herominers_data 不打网络。运行: python3 tests/test_herominers_data.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"

# 真实 herominers: 余额在顶层 unconfirmed/unlocked, 已付在 payments(元素结构防御性, 真值未确认)
D.herominers_data=lambda force=False: {
    "stats": {"hashrate": 0}, "workers": [],
    "unconfirmed": [{"amount": 150000000}], "unlocked": [{"amount": 50000000}],
    "payments": [{"amount": 900000000}],
}
v = D._herominers_view()
ck("herominers 余额=unconfirmed+unlocked=2.0", abs(v["pool_balance"]-2.0) < 1e-6)
ck("herominers 已付=payments=9.0", abs(v["pool_paid"]-9.0) < 1e-6)
ck("herominers 无 error", v["pool_error"] is None)
ck("herominers workers 是列表", isinstance(v["workers"], list))
ck("herominers total_hashrate_th 是数字", isinstance(v["total_hashrate_th"], (int,float)))

# 空列表(刚连无产出)→ 余额0 非错误
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":[],"unconfirmed":[],"unlocked":[],"payments":[]}
ve = D._herominers_view()
ck("herominers 空→余额0", ve["pool_balance"]==0.0 and ve["pool_paid"]==0.0 and ve["pool_error"] is None)

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

# workers 为 list[dict] 也能解析(hashrate 为 herominers 份额-度量, 原样 /1e12)
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":[{"name":"w1","hashrate":140000000000000}]}
v4=D._herominers_view()
ck("list workers 解析 w1≈140TH", bool(v4["workers"]) and v4["workers"][0]["name"]=="w1" and abs(v4["workers"][0]["th"]-140)<1)
# workers 为 list 含非 dict 元素不崩
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":["junk",{"name":"w2","hashrate":0}]}
v5=D._herominers_view()
ck("list 含非dict元素不崩", isinstance(v5["workers"], list))

# _sum_atomic 防御: 标量字段不崩(假如 API 返回标量而非列表)
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":[],"unconfirmed":150000000,"unlocked":0,"payments":[]}
vs = D._herominers_view()
ck("herominers 标量 unconfirmed 不崩 + 余额=1.5", vs["pool_error"] is None and abs(vs["pool_balance"]-1.5) < 1e-6)
# amount=0 不误回退到 value
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":[],"unconfirmed":[{"amount":0,"value":999}],"unlocked":[],"payments":[]}
vz = D._herominers_view()
ck("herominers amount=0 不回退 value(余额=0)", vz["pool_balance"]==0.0)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
