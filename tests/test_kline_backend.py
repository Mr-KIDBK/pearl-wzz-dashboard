#!/usr/bin/env python3
"""kline_data() 后端缓存测试(mock 网络)。
运行: python3 tests/test_kline_backend.py"""
import os, sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

FAKE = [[1780638300,"0.77","0.78","0.76","0.77","2778.0"],
        [1780638900,"0.77","0.79","0.77","0.78","3000.0"]]

class FakeResp:
    def read(self): return json.dumps(FAKE).encode()
    def __enter__(self): return self
    def __exit__(self, *a): pass

orig = urllib.request.urlopen
urllib.request.urlopen = lambda *a, **k: FakeResp()
D._kline_cache.clear()

data = D.kline_data(15)
check("返回列表", isinstance(data, list) and len(data) == 2)
check("第一条是数组", isinstance(data[0], list))
check("缓存命中", D._kline_cache.get(15) is not None)

calls = [0]
def counting(*a, **k):
    calls[0] += 1
    return FakeResp()
urllib.request.urlopen = counting
D.kline_data(15)
check("热缓存不重发请求", calls[0] == 0)

D.kline_data(15, force=True)
check("force=True 重新请求", calls[0] == 1)

# 降级: API 失败 → 返回旧缓存
D._kline_cache[15] = (FAKE, time.time() - D.KLINE_TTL[15] - 10)
urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(Exception("net"))
result = D.kline_data(15)
check("API 失败: fallback 到旧缓存", result == FAKE)

urllib.request.urlopen = orig

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
