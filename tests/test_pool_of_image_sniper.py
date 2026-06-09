#!/usr/bin/env python3
"""sniper.pool_of_image 识别四池镜像。运行: python3 tests/test_pool_of_image_sniper.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
ck("herominers", S.pool_of_image("docker.io/mrkidbk/pearl-miner-herominers:v3.3.6")=="herominers")
ck("pearlfortune", S.pool_of_image("docker.io/mrkidbk/pearl-miner-pearlfortune:v1.1.1")=="pearlfortune")
ck("twpool", S.pool_of_image("docker.io/mrkidbk/pearl-miner-twpool:v1.9.1")=="twpool")
ck("conishc→twpool", S.pool_of_image("docker.io/conishc/x:1")=="twpool")
ck("其它非空→pearlhash", S.pool_of_image("docker.io/kuzigmgm/pearl-miner:v11")=="pearlhash")
ck("空→None", S.pool_of_image("") is None)
ck("None→None", S.pool_of_image(None) is None)
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
