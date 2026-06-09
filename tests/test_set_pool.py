#!/usr/bin/env python3
"""/api/set-pool 后端: save_pool_cfg 顶层写 pool + 校验非法 pool; build_full_config 暴露 pool/pools。
monkeypatch backup_and_write 不碰真实配置。运行: python3 tests/test_set_pool.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D
fails=0
def ck(n,c):
    global fails; print(("  ✓ " if c else "  ✗ ")+n); fails+=0 if c else 1

accts = D.list_accounts()
ck("有账号可测", len(accts) >= 1)
acct = accts[0]

# 捕获写入, 不落盘
captured = {}
orig = D.backup_and_write
D.backup_and_write = lambda path, obj: captured.update({"path": path, "obj": obj})

# 合法 pool → 顶层写入 (不是 cfg[plat])
r = D.save_pool_cfg(acct, "twpool")
ck("合法 pool 返回 ok", r.get("ok") is True)
ck("pool 写到顶层 config['pool']", captured.get("obj", {}).get("pool") == "twpool")
plat = D.platform_of(acct)
ck("pool 没写进嵌套 cfg[plat]", "pool" not in (captured.get("obj", {}).get(plat, {}) or {}))

# 非法 pool → 拒绝, 不写
captured.clear()
r2 = D.save_pool_cfg(acct, "bogus_pool_xyz")
ck("非法 pool 返回 error", bool(r2.get("error")))
ck("非法 pool 未写盘", captured == {})

# 不存在账号 → 拒绝
r3 = D.save_pool_cfg("no_such_acct_zzz", "twpool")
ck("无效账号返回 error", bool(r3.get("error")))

D.backup_and_write = orig

# build_full_config 暴露 pool / pools
full = D.build_full_config()
ck("full-config 含 pools 列表", isinstance(full.get("pools"), list) and len(full["pools"]) >= 2)
ck("pools 项含 id/label", all("id" in o and "label" in o for o in full["pools"]))
ck("每账号含 pool 字段", all("pool" in v for v in full["platforms"].values()))

# 新池可切换(save_pool_cfg 走 S.POOLS 校验)
D.backup_and_write = lambda path, obj: captured.update({"path": path, "obj": obj})
r = D.save_pool_cfg(acct, "herominers")
ck("save_pool_cfg 接受 herominers", bool(r.get("ok")) and r.get("pool") == "herominers")
r2 = D.save_pool_cfg(acct, "pearlfortune")
ck("save_pool_cfg 接受 pearlfortune", bool(r2.get("ok")))
r3 = D.save_pool_cfg(acct, "nosuchpool")
ck("save_pool_cfg 拒绝未知池", bool(r3.get("error")))
D.backup_and_write = orig

if fails: print(f"\n{fails} 失败"); sys.exit(1)
print("\n全部通过")
