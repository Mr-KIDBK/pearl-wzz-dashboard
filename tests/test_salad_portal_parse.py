#!/usr/bin/env python3
"""salad_portal 纯解析函数(不依赖 playwright): parse_instances_gpu / parse_balance / session_path。
运行: python3 tests/test_salad_portal_parse.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import salad_portal as P
fails = 0
def ck(n, c):
    global fails; print(("  ✓ " if c else "  ✗ ") + n); fails += 0 if c else 1

# parse_instances_gpu: 取 instance_id -> gpu_class, 忽略缺字段
resp = {"instances": [
    {"instance_id": "iA", "gpu_class": "NVIDIA GeForce RTX 5070 Ti"},
    {"instance_id": "iB", "gpu_class": "NVIDIA GeForce RTX 4090"},
    {"instance_id": "iC"},                       # 无 gpu_class → 跳过
    {"gpu_class": "RTX 5090"},                   # 无 id → 跳过
]}
g = P.parse_instances_gpu(resp)
ck("parse_instances_gpu 取到 2 条", len(g) == 2)
ck("iA→5070Ti", g.get("iA") == "NVIDIA GeForce RTX 5070 Ti")
ck("iB→4090", g.get("iB") == "NVIDIA GeForce RTX 4090")
ck("缺字段被跳过", "iC" not in g)
ck("空响应→{}", P.parse_instances_gpu(None) == {} and P.parse_instances_gpu({}) == {})
ck("instances 非列表(str)→{}不抛错", P.parse_instances_gpu({"instances": "boom"}) == {})
ck("instances 非列表(dict)→{}不抛错", P.parse_instances_gpu({"instances": {"x": 1}}) == {})
ck("条目非 dict 被跳过", P.parse_instances_gpu({"instances": ["str", {"instance_id": "iZ", "gpu_class": "RTX 4090"}]}) == {"iZ": "RTX 4090"})

# parse_balance: {"amount": 分} → USD = 分/100
ck("2046 分 → 20.46", P.parse_balance({"amount": 2046}) == 20.46)
ck("0 → 0.0", P.parse_balance({"amount": 0}) == 0.0)
ck("缺 amount → None", P.parse_balance({}) is None and P.parse_balance(None) is None)
ck("非数字 → None", P.parse_balance({"amount": "x"}) is None)

# session_path: secrets/salad_session_<account>.json
sp = P.session_path("salad-3")
ck("session_path 落在 secrets/", "secrets" in str(sp))
ck("session_path 文件名带账号", sp.name == "salad_session_salad-3.json")

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
