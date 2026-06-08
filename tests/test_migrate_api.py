#!/usr/bin/env python3
"""/api/migrate 后端 do_migrate: 确认词 MIGRATE 校验 + 账号解析 + 调 migrate_account。
mock 掉 migrate_account/save_pool_cfg, 不碰盘/网络。运行: python3 tests/test_migrate_api.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D, sniper as S
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1

accts=D.list_accounts()
ck("有账号可测", len(accts)>=1)
acct=accts[0]

calls=[]
S.migrate_account=lambda cfg,state,account_id,target,live=True: (calls.append((account_id,target,live)), {"ok":True,"results":[],"summary":{}})[1]
saved=[]
D.save_pool_cfg=lambda a,p: (saved.append((a,p)), {"ok":True})[1]

# 错确认词 → 拒绝, 不迁移
r=D.do_migrate({"platform":acct,"target_pool":"twpool","confirm":"nope"})
ck("错确认词返回 error", bool(r.get("error")))
ck("错确认词不调 migrate_account", len(calls)==0)

# 未知池 → 拒绝
r2=D.do_migrate({"platform":acct,"target_pool":"zzz","confirm":"MIGRATE"})
ck("未知池返回 error", bool(r2.get("error")) and len(calls)==0)

# 无效账号 → 拒绝
r3=D.do_migrate({"platform":"no_such_acct_zzz","target_pool":"twpool","confirm":"MIGRATE"})
ck("无效账号返回 error", bool(r3.get("error")) and len(calls)==0)

# 正确 → 触发迁移 + 落盘
r4=D.do_migrate({"platform":acct,"target_pool":"twpool","confirm":"MIGRATE"})
ck("正确确认词 ok", r4.get("ok") is True)
ck("调 migrate_account 一次", len(calls)==1 and calls[0]==(acct,"twpool",True))
ck("落盘 pool", (acct,"twpool") in saved)

# all → 迁移所有账号
calls.clear(); saved.clear()
r5=D.do_migrate({"platform":"all","target_pool":"twpool","confirm":"MIGRATE"})
ck("all 迁移所有账号", r5.get("ok") and len(calls)==len(accts))

# save_pool_cfg 失败 → 不迁移该账号, 不中断
calls.clear()
D.save_pool_cfg=lambda a,p: {"error":"disk full"}
rfail=D.do_migrate({"platform":acct,"target_pool":"twpool","confirm":"MIGRATE"})
ck("落盘失败不调 migrate_account", len(calls)==0)
ck("落盘失败结果含 error", bool(rfail["accounts"][0]["result"].get("error")))
ck("落盘失败整体仍 ok=True(不中断)", rfail.get("ok") is True)
# 恢复 mock 供后续(若后面还有断言)
D.save_pool_cfg=lambda a,p: {"ok":True}

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
