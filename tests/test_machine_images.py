#!/usr/bin/env python3
"""account_machine_images: runpod/vast live 镜像缓存 {id:image}; 失败→{}。
mock sniper.list_runpod_pods / list_vast_instances。运行: python3 tests/test_machine_images.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D, sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
S.list_runpod_pods=lambda: [{"id":"podA","imageName":"docker.io/conishc/x:twpool"},{"id":"podB","imageName":"docker.io/kuzigmgm/pearl-miner:v11"}]
D._machine_images.clear()
m=D.account_machine_images("runpod", force=True)
ck("runpod 返回 {id:image}", m.get("podA")=="docker.io/conishc/x:twpool" and m.get("podB")=="docker.io/kuzigmgm/pearl-miner:v11")
S.list_vast_instances=lambda: [{"id":111,"image":"docker.io/conishc/x:twpool"}]
D._machine_images.clear()
mv=D.account_machine_images("vast", force=True)
ck("vast 返回 {id:image}(id 转 str)", mv.get("111")=="docker.io/conishc/x:twpool")
def boom(): raise RuntimeError("net")
S.list_runpod_pods=boom
D._machine_images.clear()
ck("抓取失败返回 {} 不抛", D.account_machine_images("runpod", force=True)=={})
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
