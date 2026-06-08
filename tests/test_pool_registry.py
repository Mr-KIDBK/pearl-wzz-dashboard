import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n);  fails+= 0 if c else 1
ck("POOLS 含 pearlhash/twpool", set(S.POOLS)>= {"pearlhash","twpool"})
ck("pearlhash reads_prl_host", S.POOLS["pearlhash"]["reads_prl_host"] is True)
ck("twpool 不读 host", S.POOLS["twpool"]["reads_prl_host"] is False)
ck("active_pool 默认 pearlhash", S.active_pool({})=="pearlhash")
ck("active_pool 取 config.pool", S.active_pool({"pool":"twpool"})=="twpool")
ck("effective_image 按 pool", S.effective_image({"pool":"twpool"})==S.POOLS["twpool"]["image"])
ck("effective_image 回退 config.image", S.effective_image({"image":"x:1"})=="x:1")
ck("未知 pool 回退 image", S.effective_image({"pool":"zzz","image":"y:2"})=="y:2")
if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
