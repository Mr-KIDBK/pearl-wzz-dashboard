#!/usr/bin/env python3
"""salad_inst_price_num: salad gpu-classes 实时价(classprice, 先解析别名)优先 → SALAD_GPU_PRICES 兜底 → None。
修 bug: RTX 4080 SUPER 在 batch 优先级组显示组级区间 $0.090–0.250/h。
根因: salad 无 'RTX 4080 SUPER' class(它按 'RTX 4080 (16 GB)' 计费) → classprice 无 rtx 4080 super;
      且 SALAD_GPU_PRICES 兜底表缺 batch 档 → .get('batch')=None → 回退组级区间 label。
修法: 别名 rtx 4080 super → rtx 4080, 直接复用 salad RTX 4080 class 的实时(batch)价 0.11。
运行: uv run python tests/test_salad_inst_price.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# salad batch 组真实 classprice(gpu_key 去 '(16 GB)' 后缀): RTX 4080=0.11, 无 rtx 4080 super
cp = {"rtx 4080": 0.11, "rtx 4090": 0.16, "rtx 5090": 0.25, "rtx 5070 ti": 0.10, "rtx 4070 ti super": 0.09}

# 核心修复: 4080 SUPER 别名→4080 命中 classprice batch 价(此前 None → 组级区间)
ck("4080 SUPER 别名复用 salad RTX 4080 实时价 batch=0.11",
   D.salad_inst_price_num("RTX 4080 SUPER", cp, "batch") == 0.11)
ck("4080 SUPER 不再 None(不回退组级区间)",
   D.salad_inst_price_num("RTX 4080 SUPER", cp, "batch") is not None)

# 回归: 有 class 的卡照常走 classprice 实时价, 别名不误伤
ck("RTX 4090 走 classprice batch=0.16", D.salad_inst_price_num("RTX 4090", cp, "batch") == 0.16)
ck("RTX 4080 走 classprice batch=0.11", D.salad_inst_price_num("RTX 4080", cp, "batch") == 0.11)

# 回归: classprice 缺该卡时退 SALAD_GPU_PRICES 兜底(别名也在兜底路径生效)
ck("classprice 空→4080 SUPER 别名兜底到 rtx 4080 high=0.28",
   D.salad_inst_price_num("RTX 4080 SUPER", {}, "high") == 0.28)
ck("RTX 4070 Ti Super 不被误降级(独立兜底 high=0.26)",
   D.salad_inst_price_num("RTX 4070 Ti Super", {}, "high") == 0.26)

# 查不到 → None(让上层回退组级 label)
ck("未知卡 + classprice 空 + 兜底无 → None",
   D.salad_inst_price_num("RTX 9999", {}, "batch") is None)

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
