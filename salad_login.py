#!/usr/bin/env python3
"""一次性: 有头登录各 salad 账号, 保存浏览器会话到 secrets/(供 dashboard 常驻 headless 复用)。
用法: python3 salad_login.py
依赖: pip install -r requirements.txt && playwright install chromium"""
import dashboard as D
import salad_portal


def main():
    accounts = []
    for acct in D.list_accounts():
        if D.platform_of(acct) != "salad":
            continue
        scfg = D.read_config(acct).get("salad", {}) or {}
        accounts.append({"account": acct,
                         "org": scfg.get("organization_name"),
                         "session_path": str(salad_portal.session_path(acct))})
    if not accounts:
        print("未找到 salad 账号(configs/config.salad*.json)")
        raise SystemExit(1)
    print(f"将依次登录 {len(accounts)} 个 salad 账号: {[a['account'] for a in accounts]}")
    salad_portal.login_accounts(accounts)
    print("\n全部完成。重启 dashboard 生效。")


if __name__ == "__main__":
    main()
