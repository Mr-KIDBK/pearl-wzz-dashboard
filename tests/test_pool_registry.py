#!/usr/bin/env python3
"""矿池注册表测试: POOLS 常量、active_pool()、effective_image() 的正确性。
运行: python3 tests/test_pool_registry.py"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n);  fails+= 0 if c else 1
ck("POOLS 含 pearlhash/twpool", set(S.POOLS)>= {"pearlhash","twpool"})
ck("pearlhash reads_prl_host", S.POOLS["pearlhash"]["reads_prl_host"] is True)
ck("twpool 不读 host", S.POOLS["twpool"]["reads_prl_host"] is False)
ck("active_pool 默认 pearlfortune", S.active_pool({})=="pearlfortune")
ck("active_pool 取 config.pool", S.active_pool({"pool":"twpool"})=="twpool")
ck("effective_image 按 pool", S.effective_image({"pool":"twpool"})==S.POOLS["twpool"]["image"])
ck("effective_image 回退 config.image", S.effective_image({"image":"x:1"})=="x:1")
ck("未知 pool 回退 image", S.effective_image({"pool":"zzz","image":"y:2"})=="y:2")
ck("active_pool(None) 返回默认 pearlfortune", S.active_pool(None)=="pearlfortune")
ck("active_pool 空白符也返回默认 pearlfortune", S.active_pool({"pool":"  "})=="pearlfortune")
ck("effective_image(None) 返回 None", S.effective_image(None) is None)
# 新池: herominers / pearlfortune
ck("POOLS 含 herominers", "herominers" in S.POOLS)
ck("POOLS 含 pearlfortune", "pearlfortune" in S.POOLS)
ck("herominers image 正确", S.POOLS["herominers"]["image"] == "docker.io/mrkidbk/pearl-miner-herominers:v3.3.6")
ck("pearlfortune image 正确", S.POOLS["pearlfortune"]["image"] == "docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.2")
ck("herominers reads_prl_host=False", S.POOLS["herominers"]["reads_prl_host"] is False)
ck("pearlfortune reads_prl_host=False", S.POOLS["pearlfortune"]["reads_prl_host"] is False)
ck("active_pool 认 herominers", S.active_pool({"pool": "herominers"}) == "herominers")
ck("effective_image 取 pearlfortune 镜像", S.effective_image({"pool": "pearlfortune"}) == "docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.2")
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
