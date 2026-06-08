#!/usr/bin/env python3
"""twpool_data: 查 worker_stats 并缓存, 结构含 reported/balance/paid。
mock urllib.request.urlopen 不打网络。运行: python3 tests/test_twpool_data.py"""
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
addr="prl1pTESTADDR"
FAKE={"balance":885.0,"paid":24416.4,"isOnline":True,
      "reported":{f"{addr}.gpu10":{"hs":140000000000000,"at":1}}}
class R:
    def read(self): return json.dumps(FAKE).encode()
    def __enter__(self): return self
    def __exit__(self,*a): pass
D.prl_address=lambda: addr
_open=urllib.request.urlopen
urllib.request.urlopen=lambda *a,**k: R()
D._twpool["data"]=None; D._twpool["ts"]=0.0
d=D.twpool_data(force=True)
ck("返回 dict 含 reported", isinstance(d,dict) and "reported" in d)
ck("balance/paid 在", d.get("balance")==885.0 and d.get("paid")==24416.4)
ck("reported 含 gpu10", f"{addr}.gpu10" in d["reported"])

# 缓存命中: 第二次 force=False 不再发请求(返回同一缓存对象)
calls={"n":0}
def counting_open(*a,**k):
    calls["n"]+=1; return R()
urllib.request.urlopen=counting_open
D._twpool["data"]=None; D._twpool["ts"]=0.0
D.twpool_data(force=True)          # 第一次真查
n1=calls["n"]
D.twpool_data(force=False)         # 未过期 → 命中缓存, 不增
ck("缓存命中不重复发请求", calls["n"]==n1)

# addr 为空 → 返回 {} 不发请求
D.prl_address=lambda: ""
D._twpool["data"]=None; D._twpool["ts"]=0.0
ck("addr 为空返回空 dict", D.twpool_data(force=True)=={})

# 网络异常 → 存 {"_error":...}, 不抛
addr2="prl1pERR"
D.prl_address=lambda: addr2
def boom(*a,**k): raise OSError("net down")
urllib.request.urlopen=boom
D._twpool["data"]=None; D._twpool["ts"]=0.0
e=D.twpool_data(force=True)
ck("网络异常存 _error 不抛", isinstance(e,dict) and "_error" in e)

urllib.request.urlopen=_open
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
