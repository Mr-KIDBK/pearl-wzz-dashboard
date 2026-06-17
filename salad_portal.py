#!/usr/bin/env python3
"""Salad portal-api 会话桥(Playwright)。
- 纯解析函数(parse_instances_gpu/parse_balance)+ 会话文件路径: 不依赖 playwright, 可直接单测。
- 登录(login_accounts)/常驻抓取(run_manager): 延迟 import playwright; playwright 缺失时优雅降级。
所有第三方端点见 docs/superpowers/specs/2026-06-08-salad-gpu-via-portal-playwright-design.md(均经实测)。"""
import sys
import urllib.parse
from pathlib import Path

SESSION_DIR = Path(__file__).resolve().parent / "secrets"

def session_path(account_id):
    """该 salad 账号的浏览器会话文件路径(gitignore 的 secrets/ 下)。"""
    return SESSION_DIR / f"salad_session_{account_id}.json"

def should_relogin(missing, consecutive_empty, last_attempt_epoch, now, empty_threshold=2, cooldown=1800):
    """是否该触发 scid 重登: (会话缺失 或 连续抓空≥阈值=scid过期) 且 距上次重登尝试≥冷却。
    纯函数, 可单测。冷却防止登录失败后每轮疯狂弹窗。"""
    needs = bool(missing) or consecutive_empty >= empty_threshold
    cool_ok = (now - (last_attempt_epoch or 0)) >= cooldown
    return bool(needs and cool_ok)

def parse_instances_gpu(resp):
    """portal-api /instances 响应 → {instance_id: gpu_class}; 缺 id 或 gpu_class 的条目跳过。
    对畸形响应(instances 非列表、条目非 dict)健壮: 跳过而非抛错。"""
    out = {}
    items = (resp or {}).get("instances")
    for inst in (items if isinstance(items, list) else []):
        if not isinstance(inst, dict):
            continue
        iid = str(inst.get("instance_id") or inst.get("id") or "")
        gc = inst.get("gpu_class")
        if iid and gc:
            out[iid] = gc
    return out

def parse_balance(resp):
    """portal-api credits-balance 响应 {"amount": 分} → USD(分/100), 缺/非数字 → None。"""
    amt = (resp or {}).get("amount")
    if amt is None:
        return None
    try:
        return round(float(amt) / 100.0, 2)
    except (TypeError, ValueError):
        return None

def _log(msg):
    print(f"[salad_portal] {msg}", file=sys.stderr, flush=True)

def login_accounts(accounts):
    """有头多账号登录: 每账号一个隔离 context(独立 cookie jar, 互不踢), 用户人工登录后存 storage_state。
    accounts: [{"account": str, "org": str, "session_path": str}]。"""
    from playwright.sync_api import sync_playwright
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(headless=False)
            for a in accounts:
                ctx = browser.new_context()
                try:
                    page = ctx.new_page()
                    page.goto("https://portal.salad.com/", wait_until="domcontentloaded")
                    print(f"\n[{a['account']}] 请在弹出的窗口登录该账号 (org={a.get('org') or '未配置'})。"
                          f"\n登录进入 portal 首页后, 回到终端按回车保存会话...", flush=True)
                    input()
                    ctx.storage_state(path=a["session_path"])
                    print(f"[{a['account']}] 会话已保存 → {a['session_path']}", flush=True)
                finally:
                    ctx.close()
        finally:
            if browser:
                browser.close()

PORTAL_BASE = "https://portal-api.salad.com/api/portal"
_FETCH_JS = "async (u) => { const r = await fetch(u, {credentials:'include'}); return r.ok ? await r.json() : null; }"

def _refresh_account(ctx, a, groups_fn, on_update):
    """单账号一轮: 加载 portal 页(续 cf_clearance)→ 页内 fetch 余额 + 各组实例 gpu_class → on_update。"""
    org = a["org"]; proj = a.get("project") or "default"
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f"https://portal.salad.com/organizations/{org}",
              wait_until="domcontentloaded", timeout=30000)
    bal_url = f"{PORTAL_BASE}/organizations/{org}/billing-profile/credits-balance"
    balance = parse_balance(page.evaluate(_FETCH_JS, bal_url))
    gpu_map = {}
    for g in (groups_fn(a["account"]) or []):
        gseg = urllib.parse.quote(str(g), safe='')
        url = f"{PORTAL_BASE}/organizations/{org}/projects/{proj}/containers/{gseg}/instances"
        gpu_map.update(parse_instances_gpu(page.evaluate(_FETCH_JS, url)))
    if balance is None and not gpu_map:
        _log(f"{a['account']} 抓取为空(scid 可能过期)")
    on_update(a["account"], gpu_map or None, balance)
    return gpu_map or None, balance

def auto_login(account, org, session_path, timeout=600, poll=5):
    """半自动重登: 弹有头 Chromium 到 portal, 自动轮询 portal-api 检测登录完成(替代 input, 后台进程可用),
    成功存 storage_state。返回 True=成功; False=超时/无GUI环境/异常(降级)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log(f"{account} auto_login 跳过: playwright 未装"); return False
    import time as _t
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    bal_url = f"{PORTAL_BASE}/organizations/{org}/billing-profile/credits-balance"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=False)
            except Exception as e:
                _log(f"{account} auto_login 弹窗失败(无GUI环境?降级): {e}"); return False
            try:
                ctx = browser.new_context(); page = ctx.new_page()
                page.goto(f"https://portal.salad.com/organizations/{org}", wait_until="domcontentloaded", timeout=30000)
                _log(f"{account} ⚠ scid 失效, 请在弹窗人工登录(Turnstile/OTP), 完成后自动续上...")
                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        if parse_balance(page.evaluate(_FETCH_JS, bal_url)) is not None:
                            ctx.storage_state(path=str(session_path))
                            _log(f"{account} ✅ 登录成功, 会话已存 → {session_path}"); return True
                    except Exception:
                        pass
                    _t.sleep(poll)
                _log(f"{account} auto_login 超时({timeout}s)未登录完"); return False
            finally:
                browser.close()
    except Exception as e:
        _log(f"{account} auto_login 异常: {type(e).__name__}: {e}"); return False


def run_manager(accounts, interval, groups_fn, on_update, stop_event):
    """常驻: 一个 headless chromium + 每账号一个隔离 context(storage_state)。
    每账号维护 consecutive_empty 计数 + last_relogin 时间; 会话缺失/连续抓空≥2(scid过期) 且 过冷却 → auto_login 半自动重登。
    单账号失败只记日志不影响其它。playwright 缺失则直接返回(优雅降级)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("playwright 未安装, salad portal 抓取跳过")
        return
    import time as _t
    from pathlib import Path
    empty_cnt = {a["account"]: 0 for a in accounts}
    last_relogin = {a["account"]: 0.0 for a in accounts}
    def _try_relogin(a, missing):
        if should_relogin(missing, empty_cnt.get(a["account"], 0), last_relogin[a["account"]], _t.time()):
            last_relogin[a["account"]] = _t.time()
            if auto_login(a["account"], a["org"], a["session_path"]):
                empty_cnt[a["account"]] = 0
                return True
        return False
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            _log(f"启动 chromium 失败({e}); 先跑 `playwright install chromium`")
            return
        def _load(a):
            if not Path(a["session_path"]).exists():
                if not _try_relogin(a, True):
                    return None
            try:
                return browser.new_context(storage_state=a["session_path"])
            except Exception as e:
                _log(f"{a['account']} 载入会话失败({e}); 重跑 salad_login.py")
                return None
        ctxs = {a["account"]: _load(a) for a in accounts}
        try:
            while not stop_event.is_set():
                for a in accounts:
                    ctx = ctxs.get(a["account"])
                    if not ctx:
                        ctxs[a["account"]] = _load(a)
                        continue
                    try:
                        gm, bal = _refresh_account(ctx, a, groups_fn, on_update)
                        if bal is None and not gm:
                            empty_cnt[a["account"]] += 1
                            if empty_cnt[a["account"]] >= 2 and _try_relogin(a, False):
                                try:
                                    ctxs[a["account"]] = browser.new_context(storage_state=a["session_path"])
                                except Exception:
                                    pass
                        else:
                            empty_cnt[a["account"]] = 0
                    except Exception as e:
                        _log(f"{a['account']} portal 刷新失败: {type(e).__name__}: {e}")
                stop_event.wait(interval)
        finally:
            try:
                browser.close()
            except Exception:
                pass
