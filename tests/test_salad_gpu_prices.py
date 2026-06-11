#!/usr/bin/env python3
"""SALAD_GPU_PRICES 兜底价: RTX 4080 SUPER(salad 无此 class, 按 RTX 4080 计费)能查到价 = RTX 4080。
salad 实例定价回退到矿池上报型号 'RTX 4080 SUPER' 时, 此前查不到 → price None(成本漏算)。
运行: uv run python tests/test_salad_gpu_prices.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1

k = D.gpu_key("RTX 4080 SUPER")
ck("gpu_key(RTX 4080 SUPER) = 'rtx 4080 super'", k=="rtx 4080 super")
ck("SALAD_GPU_PRICES 含 rtx 4080 super(兜底不再 None)", k in D.SALAD_GPU_PRICES)
ck("4080 SUPER 价 == RTX 4080 价(salad 同 class 计费)", D.SALAD_GPU_PRICES.get(k)==D.SALAD_GPU_PRICES.get("rtx 4080"))
# 矿池上报 'NVIDIA GeForce RTX 4080 SUPER'; dashboard 定价前去 'NVIDIA GeForce ' 前缀, 验证去前缀后命中
g = "NVIDIA GeForce RTX 4080 SUPER".replace("NVIDIA GeForce ", "").strip()
ck("去前缀后命中兜底(price 非 None)", D.SALAD_GPU_PRICES.get(D.gpu_key(g), {}).get("low") is not None)
# 回归: RTX 4070 Ti Super 是 salad 独立 class(价≠4070 Ti), 不能被误降级
ck("4070 Ti Super 仍独立(价≠4070 Ti)", D.SALAD_GPU_PRICES.get("rtx 4070 ti super")!=D.SALAD_GPU_PRICES.get("rtx 4070 ti"))

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
