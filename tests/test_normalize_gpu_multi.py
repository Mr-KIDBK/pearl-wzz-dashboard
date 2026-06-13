#!/usr/bin/env python3
"""normalize_gpu 对「多卡逗号串」(salad 弹性多 GPU 组的 gpu 字段)应返回空,而非误归一成第一个命中的型号。
bug: normalize_gpu 把 'RTX 4090,RTX 5090,...' 拼成长串 → 'if "5090" in compact' 第一个命中 → 归一成 RTX 5090
→ gpu_map_value 取到 RTX 5090 的最严阈值(300), 健康单卡被误判低效。
修法: 检测到逗号(多卡组)→ 返回 '' → gpu_map_value 回退 default(组级阈值)。
运行: python3 tests/test_normalize_gpu_multi.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# 核心修复: 多卡逗号串 → 空(不归一成单卡型号)
ck("逗号串(5卡)→ 空", S.normalize_gpu("RTX 4090,RTX 5090,RTX 5070 Ti,RTX 4080,RTX 4070 Ti Super") == "")
ck("逗号串(含5090在中间)不被归一成 RTX 5090", S.normalize_gpu("RTX 4090,RTX 5090,RTX 4080") != "RTX 5090")
ck("带 NVIDIA GeForce 前缀的逗号串 → 空", S.normalize_gpu("NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 5090") == "")

# 回归: 单卡照常归一
ck("单卡 RTX 4080 照常", S.normalize_gpu("RTX 4080") == "RTX 4080")
ck("单卡 RTX 5090 照常", S.normalize_gpu("RTX 5090") == "RTX 5090")
ck("单卡带前缀照常", S.normalize_gpu("NVIDIA GeForce RTX 4070 Ti SUPER") == "RTX 4070 Ti Super")
ck("空输入 → 空", S.normalize_gpu("") == "")

# 集成: gpu_map_value(逗号串) 回退 default, 不误中 5090 的 300
mh = {"RTX 5090": 300, "RTX 4080": 170, "RTX 4070 Ti Super": 130}
ck("gpu_map_value(逗号串) 回退 default(不误取 5090=300)",
   S.gpu_map_value("RTX 4090,RTX 5090,RTX 4080", mh, 130) == 130)
ck("gpu_map_value(单卡 4080) 仍命中 170", S.gpu_map_value("RTX 4080", mh, 130) == 170)

if fails:
    print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
