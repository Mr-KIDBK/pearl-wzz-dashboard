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
urllib.request.urlopen=_open
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
