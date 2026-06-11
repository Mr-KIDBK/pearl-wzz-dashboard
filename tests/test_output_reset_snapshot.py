#!/usr/bin/env python3
"""update_output_snapshot: 滚动快照(节流5min/裁剪4h)+ 最近3h产出差。
运行: uv run python tests/test_output_reset_snapshot.py"""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1
class FT:
    t=2_000_000
    @staticmethod
    def time(): return FT.t
D.time=FT
tmp=Path(tempfile.mkdtemp()); D.STATS_PATH=tmp/"stats.json"
json.dump({}, open(D.STATS_PATH,"w"))

# 第一次: 无 3h 前点 → None; 写入一个快照
ck("首次无3h前点 → None", D.update_output_snapshot(10.0) is None)
snaps=json.load(open(D.STATS_PATH)).get("output_snapshots")
ck("首次写入1个快照", isinstance(snaps,list) and len(snaps)==1 and abs(snaps[0]["out"]-10.0)<1e-9)
# 同一时刻再调(<5min)→ 不追加新点(节流)
D.update_output_snapshot(11.0)
ck("节流: <5min 不追加", len(json.load(open(D.STATS_PATH))["output_snapshots"])==1)
# 时间前进 3h+1min, out=15 → 最近3h = 15-10 = 5
FT.t += 3*3600+60
r=D.update_output_snapshot(15.0)
ck("最近3h产出 = 15-10 = 5.0", r is not None and abs(r-5.0)<1e-6)
# 裁剪: 时间前进 5h, 老点(>4h)被删
FT.t += 5*3600
D.update_output_snapshot(20.0)
ss=json.load(open(D.STATS_PATH))["output_snapshots"]
ck("裁剪 >4h 老点", all(x["ts"] >= FT.t-4*3600 for x in ss))

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
