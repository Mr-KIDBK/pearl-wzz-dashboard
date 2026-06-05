#!/usr/bin/env python3
"""实时币价拉取测试(mock 网络, 不打真实 API)。
运行: python3 tests/test_coin_price.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

# --- mock safetrade response ---
FAKE_RESP = json.dumps({"at": "1780638388", "ticker": {
    "buy": "0.78", "sell": "0.77", "last": "0.80",
    "high": "0.81", "low": "0.74"
}}).encode()

import urllib.request
class FakeResp:
    def read(self): return FAKE_RESP
    def __enter__(self): return self
    def __exit__(self, *a): pass
    status = 200

orig_urlopen = urllib.request.urlopen
urllib.request.urlopen = lambda *a, **k: FakeResp()
D._price_cache.clear()

p = D.fetch_coin_price()
check("fetch_coin_price 返回 ticker.last", p == 0.80)
check("缓存已写入", D._price_cache.get("prl") is not None)

# 热缓存不重复调用 API
calls = [0]
def counting_urlopen(*a, **k):
    calls[0] += 1
    return FakeResp()
urllib.request.urlopen = counting_urlopen
D.fetch_coin_price()  # 应命中缓存
check("热缓存: 不再发 HTTP 请求", calls[0] == 0)

# force=True 绕过缓存
D.fetch_coin_price(force=True)
check("force=True: 重新发请求", calls[0] == 1)

# API 失败时 fallback 到上次缓存值
D._price_cache["prl"] = (0.75, time.time() - D.PRICE_STALE_MAX - 10)
urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(Exception("net err"))
p2 = D.fetch_coin_price()
check("API 失败: fallback 到缓存旧值", p2 == 0.75)

urllib.request.urlopen = orig_urlopen

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
