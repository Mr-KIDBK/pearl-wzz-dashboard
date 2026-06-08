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
