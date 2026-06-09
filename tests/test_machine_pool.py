#!/usr/bin/env python3
"""池判定: pool_of_image(镜像优先) / pool_of_worker(兜底) / machine_pool。
运行: python3 tests/test_machine_pool.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
ck("conishc twpool 镜像→twpool", D.pool_of_image("docker.io/conishc/pearl-miner:twpool-v1.9.0-auto")=="twpool")
ck("新镜像 mrkidbk/pearl-miner-twpool→twpool", D.pool_of_image("docker.io/mrkidbk/pearl-miner-twpool:v1.9.1")=="twpool")
ck("kuzigmgm→pearlhash", D.pool_of_image("docker.io/kuzigmgm/pearl-miner:v11")=="pearlhash")
ck("mrkidbk→pearlhash", D.pool_of_image("docker.io/mrkidbk/pearl-miner:latest")=="pearlhash")
ck("空镜像→None", D.pool_of_image("") is None and D.pool_of_image(None) is None)
addr="prl1pX"; D.prl_address=lambda: addr
D.twpool_data=lambda force=False: {"reported":{f"{addr}.gpu10":{"hs":1}}}
D.pool_data=lambda force=False: {"connected_workers":[{"worker_name":"rp1-runpod-x"}]}
ck("worker 在 twpool→twpool", D.pool_of_worker("gpu10")=="twpool")
ck("worker 在 pearlhash→pearlhash", D.pool_of_worker("rp1-runpod-x")=="pearlhash")
D.pool_data=lambda force=False: {"connected_workers":[{"worker_name":"rp1-runpod-x-abcd1234"}]}
ck("pearlhash worker 带 -hash 后缀也匹配", D.pool_of_worker("rp1-runpod-x")=="pearlhash")
ck("worker 都不在→None", D.pool_of_worker("nobody") is None)
D.twpool_data=lambda force=False: {"reported":{}}
D.pool_data=lambda force=False: {"connected_workers":[]}
ck("有镜像按镜像", D.machine_pool("docker.io/conishc/x:twpool", "anyworker")=="twpool")
D.twpool_data=lambda force=False: {"reported":{f"{addr}.w1":{"hs":1}}}
ck("无镜像走 worker", D.machine_pool(None, "w1")=="twpool")
ck("都无→unknown", D.machine_pool(None, "ghost")=="unknown")
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
