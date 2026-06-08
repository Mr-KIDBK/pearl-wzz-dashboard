import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0

def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n)
    fails += 0 if c else 1

addr = "prl1pTESTADDR"
FAKE = {
    "hashrate": 1.0,
    "balance": 885.0,
    "paid": 24416.4,
    "isOnline": True,
    "reported": {
        f"{addr}.rp1-runpod-x": {"hs": 240000000000000, "at": 1780000000000},
        f"{addr}.rp1-runpod-y": {"hs": 0, "at": 1780000000000}
    },
    "history": {
        f"{addr}.rp1-runpod-x": [{"time": 1, "hashrate": 1e14}]
    }
}

class R:
    def read(self):
        return json.dumps(FAKE).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass

S_open = urllib.request.urlopen
urllib.request.urlopen = lambda *a, **k: R()

out = S.twpool_worker_hashrates({"prl_address": addr})

ck("返回 dict 含两个 worker", set(out) == {"rp1-runpod-x", "rp1-runpod-y"})
ck("hs 转 TH/s: 240e12→240", abs(out["rp1-runpod-x"]["hashrate_th"] - 240) < 0.5)
ck("0 算力 worker 也在", out["rp1-runpod-y"]["hashrate_th"] == 0.0)
ck("schema 同 pearl(有 hashrate_th)", "hashrate_th" in out["rp1-runpod-x"])

# 边界测试
ck("空 address 返回 {}", S.twpool_worker_hashrates({"prl_address": ""}) == {})
ck("config 为 None/空 address 返回 {}", S.twpool_worker_hashrates({}) == {})

urllib.request.urlopen = S_open

if fails:
    print(f"\n{fails} 失败")
    sys.exit(1)
print("\n全部通过")
