#!/usr/bin/env python3
"""try_host_fallback 测试: host 兜底只对 runpod 生效, vast 物理上无法原地改 env 重启, 必须禁用。
mock 掉 restart_instance_with_env / notify, 不打网络。运行: python3 tests/test_host_fallback.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond: fails += 1

# 满足兜底全部前置条件的场景构造器
def make_scene():
    pcfg = {"host_fallback_host": "9.9.9.9:9000", "host_fallback_zero_seconds": 60,
            "low_efficiency_stop_seconds": 900, "host_fallback_enabled": True}
    config = {"prl_host": "1.2.3.4:9000", "vast": dict(pcfg), "runpod": dict(pcfg)}
    rented = {"zero_since_epoch": S.epoch_now() - 120,  # 0 算力已持续 120s > 60s 窗口
              "env": {"PRL_HOST": "1.2.3.4:9000", "PRL_WORKER": "w1"}, "gpu": "RTX 4090"}
    return config, rented

calls = []
S.restart_instance_with_env = lambda provider, iid, env: (calls.append((provider, iid, env)), {"ok": True})[1]
S.notify = lambda *a, **k: None

# --- vast: 即使条件全满足, 也禁用 → 返回 False, 不调用 restart, 不标 host_switched ---
config, rented = make_scene()
calls.clear()
r_vast = S.try_host_fallback(config, "vast", rented, "vast-contract-1")
check("vast 返回 False(禁用兜底, 让调用方去销毁)", r_vast is False)
check("vast 未调用 restart_instance_with_env", len(calls) == 0)
check("vast 未标记 host_switched", not rented.get("host_switched"))

# --- runpod: 同样条件下应触发兜底 → 返回 True, 调用 restart 一次 ---
config, rented = make_scene()
calls.clear()
r_rp = S.try_host_fallback(config, "runpod", rented, "pod-1")
check("runpod 返回 True(触发兜底)", r_rp is True)
check("runpod 调用 restart 一次", len(calls) == 1 and calls[0][0] == "runpod")
check("runpod env 已切到 fallback host", len(calls) == 1 and calls[0][2].get("PRL_HOST") == "9.9.9.9:9000")
check("runpod 标记 host_switched", rented.get("host_switched") is True)

# --- twpool: active_pool=twpool 不读 PRL_HOST → 即使条件全满足也禁用兜底 ---
config_tw, rented_tw = make_scene()
config_tw["pool"] = "twpool"
calls.clear()
r_tw = S.try_host_fallback(config_tw, "runpod", rented_tw, "pod-tw-1")
check("active_pool=twpool 时 host 兜底禁用(返回 False)", r_tw is False)
check("twpool 未调用 restart", len(calls) == 0)
check("twpool 未标记 host_switched", not rented_tw.get("host_switched"))

if fails:
    print(f"\n{fails} 个断言失败"); sys.exit(1)
print("\n全部通过")
