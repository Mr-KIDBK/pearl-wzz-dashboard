#!/usr/bin/env python3
"""parse_latest_hashrate 解析 pearlfortune (vllm.gpu) Salad 日志格式:
进度行含 proof_per_sec="145.11 T/s" (=145.11 TH/s, 与池 reported_hashrate 同刻度);
心跳行(rpc.ping)无算力 → None; 不误匹配 throughput_mhps / rounds_per_sec / eta。
运行: uv run python tests/test_pearlfortune_log_parse.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S

fails = 0
def ck(n, c):
    global fails
    print(("  ✓ " if c else "  ✗ ") + n)
    fails += 0 if c else 1

f = S.parse_latest_hashrate

PROGRESS = ('ts=2026-06-10T06:24:54.283 level=INFO component=vllm.gpu event=large.progress '
            'rounds_per_sec=32.99 throughput_mhps=276.78 proof_per_sec="145.11 T/s" eta=3264s')
PING = ('ts=2026-06-10T06:24:56.441 level=INFO component=rpc event=request.sent '
        'id=124 method=rpc.ping elapsed_ms="134.455µs"')

ck("进度行 proof_per_sec 145.11 TH", abs((f(PROGRESS) or 0) - 145.11) < 0.01)
ck("心跳行 → None", f(PING) is None)
ck("不误匹配 throughput_mhps/rounds_per_sec/eta(无 proof_per_sec → None)",
   f('ts=x event=large.progress rounds_per_sec=32.99 throughput_mhps=276.78 eta=3264s') is None)
ck("单位 G/s → 0.0015", abs((f('proof_per_sec="1.5 G/s"') or 0) - 0.0015) < 1e-6)
ck("单位 M/s → 0.0009", abs((f('proof_per_sec="900 M/s"') or 0) - 0.0009) < 1e-6)
ck("无引号 proof_per_sec=2.0 T/s → 2.0", abs((f('proof_per_sec=2.0 T/s') or 0) - 2.0) < 0.01)
ck("多行: 心跳行 + 进度行 → 取进度行 145.11", abs((f(PING + "\n" + PROGRESS) or 0) - 145.11) < 0.01)
# 回归: 旧格式不受影响
ck("回归 pearlhash Hashrate Total 140.5", abs((f("blah Hashrate Total = 140.5 TH/s") or 0) - 140.5) < 0.01)
ck("回归 twpool window 134.6",
   abs((f("03:59:41 | 134.6 TH/s window | 135.2 TH/s avg | shares: 143 accepted") or 0) - 134.6) < 0.01)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
