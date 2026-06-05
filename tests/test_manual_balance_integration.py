#!/usr/bin/env python3
"""手填余额接线的集成测试: build_rentals 估算 + save_platform_cfg 自动盖戳。
用临时 ROOT + tensordock 测试账号(无余额 API、读 state 不触发 live API), 不碰真实 config。
运行: python3 tests/test_manual_balance_integration.py"""
import os, sys, json, time, tempfile, datetime as dt
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

tmp = Path(tempfile.mkdtemp())
(tmp / "configs").mkdir()
# 真实 ROOT 切到 tmp; 清掉缓存避免串号
D.ROOT = tmp
D._bal.clear()
now = time.time()
asof_iso = dt.datetime.fromtimestamp(now - 3600).astimezone().isoformat(timespec="seconds")  # 1h 前

# tensordock 测试账号: 手填余额 100, 1h 前
(tmp / "configs" / "config.tensordock.json").write_text(json.dumps({
    "tensordock": {"enabled": True, "balance_usd": 100.0, "balance_asof": asof_iso}
}))
# state: 一台在跑机器, 单价 10/h
(tmp / "state.tensordock.json").write_text(json.dumps({
    "rented": [{"active": True, "external_id": "td-1", "gpu": "RTX 4090",
                "price": 10.0, "created_epoch": now - 3600}]
}))

# --- build_rentals 估算: 100 - 10*1h = 90, 标记为估算 ---
r = D.build_rentals()["tensordock"]
check("balance_estimated=True", r.get("balance_estimated") is True)
check("balance_editable=True (无 API 平台可手填)", r.get("balance_editable") is True)
check("balance_usd 原值透传给前端(供预填)", r.get("balance_usd") == 100.0)
check("burn_hourly=10", r.get("burn_hourly") == 10.0)
check("估算余额≈90 (90±0.5)", r.get("balance") is not None and abs(r["balance"] - 90.0) <= 0.5)
check("hours_left≈9", r.get("hours_left") is not None and abs(r["hours_left"] - 9.0) <= 0.2)

# --- save_platform_cfg: 改余额值 → 自动盖新 balance_asof ---
res = D.save_platform_cfg("tensordock", {"balance_usd": 50.0})
check("save ok", res.get("ok") is True)
saved = json.loads((tmp / "configs" / "config.tensordock.json").read_text())["tensordock"]
check("balance_usd 写入=50", saved.get("balance_usd") == 50.0)
check("balance_asof 自动盖新戳(≠旧值)", saved.get("balance_asof") and saved["balance_asof"] != asof_iso)

# --- save 不含 balance_usd → 不动 balance_asof ---
stamp_before = saved["balance_asof"]
D.save_platform_cfg("tensordock", {"city": "NYC"})
saved2 = json.loads((tmp / "configs" / "config.tensordock.json").read_text())["tensordock"]
check("未改余额时 balance_asof 不变", saved2.get("balance_asof") == stamp_before)

# --- 同值 save → 不重置时间戳(避免把估算清零计时重置) ---
D.save_platform_cfg("tensordock", {"balance_usd": 50.0})
saved3 = json.loads((tmp / "configs" / "config.tensordock.json").read_text())["tensordock"]
check("余额值不变时 balance_asof 不重置", saved3.get("balance_asof") == stamp_before)

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
