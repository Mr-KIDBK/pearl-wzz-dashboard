#!/usr/bin/env python3
"""build_rentals: salad 账号优先用 portal 真实余额(balance_real=True, 非估算); 无缓存回退手填估算。
运行: python3 tests/test_salad_real_balance.py"""
import os, sys, time, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

now = time.time()
asof_iso = dt.datetime.fromtimestamp(now - 3600).astimezone().isoformat(timespec="seconds")
D.list_accounts = lambda: ["salad"]
D.platform_of = lambda a: "salad"
D.account_label = lambda a: a
D.read_config = lambda a: {"salad": {"enabled": True, "balance_usd": 100.0, "balance_asof": asof_iso}}
D.rent_paused = lambda a: False
D.pid_for = lambda a: 1
D.platform_balance = lambda a, force=False: None  # salad 无 API 余额
D.account_machine_images = lambda a, force=False: {}
D.salad_live = lambda a, force=False: {"instances": [
    {"id": "i1", "gpu": "5070 Ti", "price": 0.22, "hashrate_th": 100,
     "started_epoch": now, "state": "running", "group": "g1",
     "image": "docker.io/conishc/pearl-miner:twpool"}], "counts": {}, "error": None}

# --- 有真实余额缓存 → 用真实值, 非估算, balance_real=True ---
D._salad_balance.clear()
D._salad_balance["salad"] = {"data": 20.46, "ts": now}
r = D.build_rentals()["salad"]
ck("用 portal 真实余额 20.46", r.get("balance") == 20.46)
ck("balance_real=True", r.get("balance_real") is True)
ck("balance_estimated=False(非估算)", r.get("balance_estimated") is False)
ck("有真实余额 → 隐藏手填(balance_editable=False)", r.get("balance_editable") is False)

# --- 无真实余额缓存 → 回退手填估算(100 - 0.22*1h ≈ 99.78) ---
D._salad_balance.clear()
r2 = D.build_rentals()["salad"]
ck("回退估算 balance_estimated=True", r2.get("balance_estimated") is True)
ck("回退 balance_real=False", r2.get("balance_real") is False)
ck("回退估算值≈99.78", r2.get("balance") is not None and abs(r2["balance"] - 99.78) <= 0.5)
ck("无真实余额回退 → 可手填(balance_editable=True)", r2.get("balance_editable") is True)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
