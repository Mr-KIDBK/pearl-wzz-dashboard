#!/usr/bin/env python3
"""herominers_data/_herominers_view: 余额=stats.balance(字符串原子/1e8, 实测确认),
已付=顶层 payments(原子1e8, 元素防御性, 真实非空格式待确认) + workers; Not-found 视为空非错。
(顶层 unconfirmed=[]、unlocked=区块明细串, 实测确认不用于余额。)
monkeypatch herominers_data 不打网络。运行: python3 tests/test_herominers_data.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
D.prl_address=lambda: "prl1pX"

# 真实 herominers: 余额在 stats.balance(字符串原子); 已付在 payments(元素防御性, 真值未确认);
# 顶层 unconfirmed=[] / unlocked=冒号分隔区块明细串 不再用于余额。
D.herominers_data=lambda force=False: {
    "stats": {"balance": "200000000", "hashrate": 0}, "workers": [],
    "unconfirmed": [], "unlocked": ["70502:hx:1:2:5032239:3:4:unlocked:hx:as-sg:prop:4:3:5","1781024776"],
    "payments": [{"amount": 900000000}],
}
v = D._herominers_view()
ck("herominers 余额=stats.balance=2.0", abs(v["pool_balance"]-2.0) < 1e-6)
ck("herominers 已付=payments=9.0", abs(v["pool_paid"]-9.0) < 1e-6)
ck("herominers 无 error", v["pool_error"] is None)
ck("herominers workers 是列表", isinstance(v["workers"], list))
ck("herominers total_hashrate_th 是数字", isinstance(v["total_hashrate_th"], (int,float)))

# 实测真实字符串原子 5032239 → 0.050322(代码 round 6 位)
D.herominers_data=lambda force=False: {"stats":{"balance":"5032239"},"workers":[],"payments":[]}
vb = D._herominers_view()
ck("stats.balance 字符串 5032239→0.050322", abs(vb["pool_balance"]-0.050322) < 1e-6)

# balance 缺失 / None / 非数字 → 余额0 不崩
D.herominers_data=lambda force=False: {"stats":{"hashrate":0},"workers":[],"payments":[]}
ve = D._herominers_view()
ck("无 balance→余额0", ve["pool_balance"]==0.0 and ve["pool_paid"]==0.0 and ve["pool_error"] is None)
D.herominers_data=lambda force=False: {"stats":{"balance":None},"workers":[],"payments":[]}
ck("balance None→0 不崩", D._herominers_view()["pool_balance"]==0.0)
D.herominers_data=lambda force=False: {"stats":{"balance":"abc"},"workers":[],"payments":[]}
ck("balance 非数字→0 不崩", D._herominers_view()["pool_balance"]==0.0)

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

# workers 为 list[dict] 解析(hashrate 为 herominers 份额-度量, 原样 /1e12)
D.herominers_data=lambda force=False: {"stats":{"balance":"0","hashrate":0},"workers":[{"name":"w1","hashrate":140000000000000}]}
v4=D._herominers_view()
ck("list workers 解析 w1≈140TH", bool(v4["workers"]) and v4["workers"][0]["name"]=="w1" and abs(v4["workers"][0]["th"]-140)<1)
# workers 含非 dict 元素不崩
D.herominers_data=lambda force=False: {"stats":{"balance":"0","hashrate":0},"workers":["junk",{"name":"w2","hashrate":0}]}
v5=D._herominers_view()
ck("list 含非dict元素不崩", isinstance(v5["workers"], list))

# _sum_atomic 防御(用于 payments): 标量不崩 + 已付正确
D.herominers_data=lambda force=False: {"stats":{"balance":"0"},"workers":[],"payments":900000000}
vps = D._herominers_view()
ck("payments 标量不崩 + 已付=9.0", vps["pool_error"] is None and abs(vps["pool_paid"]-9.0) < 1e-6)
# payments amount=0 不误回退到 value
D.herominers_data=lambda force=False: {"stats":{"balance":"0"},"workers":[],"payments":[{"amount":0,"value":999}]}
vz = D._herominers_view()
ck("payments amount=0 不回退 value(已付=0)", vz["pool_paid"]==0.0)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
