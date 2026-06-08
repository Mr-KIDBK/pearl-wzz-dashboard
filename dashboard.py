#!/usr/bin/env python3
"""今晚挖珍珠 // PEARL_SNIPER Dashboard (stdlib, 零依赖)。
总览(钱包/算力/租金/币 + 4 平台租用) + 配置(common + 4 平台, 结构化 + raw JSON)。
"""
import json
import os
import re
import time
import threading
import subprocess
import secrets
import hmac
import hashlib
import datetime as dt
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTROL_DIR = ROOT / "control"
STATS_PATH = ROOT / "dashboard-stats.json"
PLATFORMS = ["vast", "runpod", "tensordock", "salad"]
KEYNAME = {
    "vast": "VAST_API_KEY",
    "runpod": "RUNPOD_API_KEY",
    "tensordock": "TENSORDOCK_API_TOKEN",
    "salad": "SALAD_API_KEY",
}
COMMON_KEYS = ["image", "prl_address", "prl_host", "worker_prefix",
               "max_active_instances", "max_total_hourly_usd", "poll_seconds", "alert_url"]
# 每平台结构化暴露的特定字段: (key, type)  type in num/str/list/bool
SPECIFIC = {
    "vast": [("max_offer_price_usd", "num"), ("min_offer_price_usd", "num"),
             ("min_reliability", "num"), ("disk_gb", "num"), ("prefer_countries", "list")],
    "runpod": [("cloud_types", "list"), ("country_codes", "list"), ("container_disk_gb", "num"),
               ("create_observed_price_factor", "num"), ("short_exit_blacklist_seconds", "num")],
    "tensordock": [("excluded_states", "list"), ("storage_gb", "num"), ("vcpu_count", "num"),
                   ("ram_gb", "num"), ("seen_ttl_seconds", "num")],
    "salad": [("organization_name", "str"), ("project_name", "str"), ("include_container_groups", "list"),
              ("default_min_hashrate_th", "num"), ("per_model_threshold_enabled", "bool"),
              ("treat_missing_log_as_zero", "bool"), ("low_efficiency_stop_seconds", "num"),
              ("reallocate_cooldown_seconds", "num"), ("hashrate_watch_interval_seconds", "num"),
              ("log_lookback_seconds", "num"), ("missing_worker_as_zero", "bool"),
              ("alphapool_worker_api_enabled", "bool"), ("alphapool_reallocate_enabled", "bool"),
              ("balance_usd", "num")],
}
HAS_CREATE = {"runpod", "tensordock"}
NO_BALANCE_API = {"salad", "tensordock"}  # 无余额查询 API → 余额可在看板手填(总览内联编辑)

def platform_of(account_id):
    """salad-2 → salad ; salad → salad"""
    return re.sub(r"-\d+$", "", account_id)

def list_accounts():
    """扫描 configs/config.<X>.json(排除 *.example.json), 返回 account_id 列表, 账号1(无后缀)在前。"""
    out = []
    for p in sorted(ROOT.glob("configs/config.*.json")):
        name = p.name
        if name.endswith(".example.json"):
            continue
        out.append(name[len("config."):-len(".json")])
    return sorted(out, key=lambda a: (platform_of(a), a))

def account_label(account_id):
    """卡片/侧栏标签: 平台-标识(标识 = 自定义 account_label / salad 的 org / 账号序号)。"""
    plat = platform_of(account_id)
    cfg = read_config(account_id)
    custom = cfg.get("account_label")
    org = (cfg.get(plat, {}) or {}).get("organization_name")
    m = re.search(r"-(\d+)$", account_id)
    n = m.group(1) if m else "1"
    ident = custom or org or f"账号{n}"
    return f"{plat}-{ident}"

def key_var_for(account_id):
    """该账号 API key 的 .env 变量名: config.api_key_env 优先, 否则平台标准名。"""
    plat = platform_of(account_id)
    return read_config(account_id).get("api_key_env") or KEYNAME.get(plat, "")

def env_quote(v):
    """单引号包裹值, 内部 ' 转义为 '\\''; 让 .env 被 shell source 时安全、防注入。"""
    return "'" + str(v).replace("'", "'\\''") + "'"

def env_unquote(v):
    v = str(v).strip()
    if len(v) >= 2 and v.startswith("'") and v.endswith("'"):
        return v[1:-1].replace("'\\''", "'")
    return v

def load_conf():
    """看板登录配置从 .env 读(DASHBOARD_USER / DASHBOARD_PASSWORD / DASHBOARD_PORT)。"""
    e = {}
    try:
        for line in open(ROOT / ".env"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e[k.strip()] = env_unquote(v.strip())
    except Exception:
        pass
    g = lambda k, d: e.get(k) or os.environ.get(k) or d
    try:
        port = int(g("DASHBOARD_PORT", 8787))
    except Exception:
        port = 8787
    return {"user": g("DASHBOARD_USER", "admin"), "password": g("DASHBOARD_PASSWORD", "123456"), "port": port}

CONF = load_conf()
SESS_TTL = 2592000  # 30 天;签名 cookie 无状态, 重启不掉登录
_pool = {"data": None, "ts": 0.0}
POOL_STALE_MAX = 90.0  # serve-stale: 后台刷新; 超此值才在请求线程兜底重拉
_lock = threading.Lock()
# pearl 折算币价 — 实时从 SafeTrade REST API 拉取(ticker.last), 失败时 fallback 到旧缓存或默认值
COIN_PRICE_USD = float(os.environ.get("COIN_PRICE_USD") or 0.75)  # 兜底默认值(仅在首次拉取失败时用)
PRICE_TTL      = 60.0   # 后台刷新间隔内视为新鲜; 请求线程直接读缓存
PRICE_STALE_MAX = 300.0  # 超此值(后台异常)才在请求线程同步重拉
_SAFETRADE_URL = "https://safetrade.com/api/v2/peatio/public/markets/prlusdt/tickers"
_price_cache: dict = {}  # {"prl": (price_float, ts)}
_kline_cache: dict = {}  # {period_int: (data_list, ts)}
KLINE_TTL = {15: 30, 60: 120, 240: 300, 1440: 600}  # 各周期缓存秒数
_KLINE_BASE = "https://safetrade.com/api/v2/trade/public/markets/prlusdt/k-line"
_KLINE_LIMITS = {15: 288, 60: 240, 240: 180, 1440: 180}  # 各周期拉取条数


# ---------- 读 ----------
def read_json(p, default):
    try:
        return json.load(open(p))
    except Exception:
        return default

def cfg_path(plat):
    return ROOT / f"configs/config.{plat}.json"

def prl_address():
    for a in list_accounts():
        w = read_config(a).get("prl_address")
        if w:
            return w
    return ""

def read_state(plat):
    return read_json(ROOT / f"state.{plat}.json", {})

def read_config(plat):
    return read_json(cfg_path(plat), {})

def read_env():
    m = {}
    try:
        for line in open(ROOT / ".env"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            m[k.strip()] = env_unquote(v.strip())
    except Exception:
        pass
    return m

def set_env_key(name, value):
    path = ROOT / ".env"
    try:
        lines = open(path).read().splitlines()
    except Exception:
        lines = []
    out, found = [], False
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == name:
            out.append(f"{name}={env_quote(value)}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={env_quote(value)}")
    open(path, "w").write("\n".join(out) + "\n")

def set_dashboard_password(newpw):
    newpw = str(newpw or "")
    if len(newpw) < 4:
        return {"error": "密码至少 4 位"}
    try:
        set_env_key("DASHBOARD_PASSWORD", newpw)
    except Exception as e:
        return {"error": f"写入失败: {e}"}
    CONF["password"] = newpw
    return {"ok": True}

def tail_log(plat, lines=300):
    p = ROOT / f"logs/{plat}.log"
    if not p.exists():
        return f"(日志文件不存在: logs/{plat}.log;该平台可能还没启动过)"
    try:
        lines = max(1, min(int(lines), 2000))
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, max(8000, lines * 220))
            f.seek(size - block)
            data = f.read().decode("utf-8", "replace")
        rows = data.splitlines()
        if size > block and rows:
            rows = rows[1:]  # 丢掉可能被截断的首行
        return "\n".join(rows[-lines:]) or "(日志为空)"
    except Exception as e:
        return f"(读取失败: {type(e).__name__}: {e})"

def hashrate_th(raw):
    try:
        return float(raw) / 1e12
    except Exception:
        return 0.0

def pid_for(plat):
    try:
        out = subprocess.run(["pgrep", "-f", f"config.{plat}.json"], capture_output=True, text=True)
        pids = [x for x in out.stdout.split() if x]
        return pids[0] if pids else None
    except Exception:
        return None

def rent_paused(plat):
    return (CONTROL_DIR / f"{plat}.rent-paused").exists()

def pool_data(force=False):
    now = time.time()
    if _pool["data"] is not None and not force and (now - _pool["ts"] < POOL_STALE_MAX):
        return _pool["data"]
    addr = prl_address()
    data = {}
    if addr:
        try:
            req = urllib.request.Request(
                f"https://pearlhash.xyz/api/account/{urllib.parse.quote(addr)}",
                headers={"User-Agent": "sniper-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            data = {"_error": f"{type(e).__name__}: {e}"}
    _pool["data"] = data
    _pool["ts"] = now
    return data


_twpool = {"data": None, "ts": 0.0}
TWPOOL_API = "https://api.tw-pool.com/api/worker_stats"

def twpool_data(force=False):
    """查 twpool per-worker 算力 + 余额, serve-stale 缓存(同 pool_data)。
    返回 {"reported": {...}, "balance": <PRL>, "paid": <PRL>, ...} 或 {"_error": ...}。"""
    now = time.time()
    if _twpool["data"] is not None and not force and (now - _twpool["ts"] < POOL_STALE_MAX):
        return _twpool["data"]
    addr = prl_address()
    data = {}
    if addr:
        try:
            url = f"{TWPOOL_API}?address={urllib.parse.quote(addr)}&mode=realtime&excludeWorker=false&selectPool=pearl"
            req = urllib.request.Request(url, headers={"User-Agent": "sniper-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            data = {"_error": f"{type(e).__name__}: {e}"}
    _twpool["data"] = data
    _twpool["ts"] = now
    return data


# ---------- Salad 实时 ----------
_salad = {}  # account_id -> {"data", "ts"}
SALAD_STALE_MAX = 90.0   # 后台每 REFRESH_INTERVAL 强制刷新; 仅缓存超此值(后台异常)才在请求线程同步兜底重算
SALAD_WORKERS = 8        # 每账号容器组并发拉取的线程数
REFRESH_INTERVAL = 30.0  # 后台刷新所有缓存(salad/余额/矿池)的周期(秒); 循环为 refresh→sleep, 轮次不重叠

def salad_get(url, key):
    req = urllib.request.Request(url, headers={"Salad-Api-Key": key, "User-Agent": "sniper-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))

def iso_to_epoch(s):
    try:
        s2 = re.sub(r'(\.\d{6})\d+', r'\1', str(s))
        return dt.datetime.fromisoformat(s2).timestamp()
    except Exception:
        return None

def gpu_key(name):
    """GPU 名归一化: 去掉 ' (xx GB)' 后缀、结尾 'GPU' 字样(移动卡如 'RTX 5090 Laptop GPU')并小写, 用于跨数据源匹配。"""
    s = re.sub(r"\s*\(.*?\)\s*$", "", str(name or "")).strip().lower()
    return re.sub(r"\s+gpu$", "", s).strip()

# Salad 官网各 GPU 档价(low/medium/high), 仅作 gpu-classes API 失败时的兜底
SALAD_GPU_PRICES = {
    "rtx 5090":          {"low": 0.31,  "medium": 0.38,  "high": 0.45},
    "rtx 5090 laptop":   {"low": 0.16,  "medium": 0.22,  "high": 0.28},
    "rtx 5080":          {"low": 0.25,  "medium": 0.335, "high": 0.42},
    "rtx 5070 ti":       {"low": 0.16,  "medium": 0.22,  "high": 0.28},
    "rtx 5070":          {"low": 0.133, "medium": 0.187, "high": 0.24},
    "rtx 5060 ti":       {"low": 0.107, "medium": 0.143, "high": 0.18},
    "rtx 4090":          {"low": 0.207, "medium": 0.253, "high": 0.30},
    "rtx 4080":          {"low": 0.167, "medium": 0.223, "high": 0.28},
    "rtx 4070 ti super": {"low": 0.147, "medium": 0.203, "high": 0.26},
    "rtx 4070 ti":       {"low": 0.133, "medium": 0.187, "high": 0.24},
    "rtx 4070":          {"low": 0.12,  "medium": 0.17,  "high": 0.22},
    "rtx 4060 ti":       {"low": 0.127, "medium": 0.173, "high": 0.22},
    "rtx 3090 ti":       {"low": 0.16,  "medium": 0.22,  "high": 0.28},
    "rtx 3090":          {"low": 0.143, "medium": 0.197, "high": 0.25},
    "rtx 3080 ti":       {"low": 0.12,  "medium": 0.16,  "high": 0.20},
    "rtx 3060":          {"low": 0.053, "medium": 0.067, "high": 0.08},
    "rtx 2080 ti":       {"low": 0.073, "medium": 0.087, "high": 0.10},
    "rtx a5000":         {"low": 0.143, "medium": 0.197, "high": 0.25},
}

_gpucls = {}  # org -> {"data", "ts"}

def salad_gpu_prices(base, org, key):
    """{uuid: {name, prices:{priority:price}}} ，缓存 10min(按 org)。"""
    now = time.time()
    slot = _gpucls.get(org)
    if slot and now - slot["ts"] < 600:
        return slot["data"]
    out = {}
    try:
        d = salad_get(f"{base}/organizations/{org}/gpu-classes", key)
        for g in (d.get("items") or []):
            pr = {p.get("priority"): p.get("price") for p in (g.get("prices") or [])}
            out[g.get("id")] = {"name": g.get("name"), "prices": pr}
    except Exception:
        pass
    _gpucls[org] = {"data": out, "ts": now}
    return out

def salad_live(account_id="salad", force=False):
    """读 salad 缓存(serve-stale, 永不在请求线程阻塞); 后台线程每 REFRESH_INTERVAL 强制刷新。
    仅冷启动(无缓存)或后台异常致缓存超 SALAD_STALE_MAX 兜底时才同步重算。"""
    now = time.time()
    slot = _salad.get(account_id)
    if slot and not force and (now - slot["ts"] < SALAD_STALE_MAX):
        return slot["data"]
    data = _salad_compute(account_id)
    _salad[account_id] = {"data": data, "ts": now}
    return data

def _salad_compute(account_id):
    """实际拉取 salad 数据: 各容器组的 /{组} + /{组}/instances 用线程池并发(原为串行, 组多时很慢)。"""
    res = {"instances": [], "counts": {}, "error": None, "price_label": None, "gpu_classes": []}
    plat = platform_of(account_id)
    scfg = read_config(account_id).get(plat, {})
    kv = key_var_for(account_id)
    key = read_env().get(kv) or os.environ.get(kv, "")
    org, proj = scfg.get("organization_name"), scfg.get("project_name")
    if not (key and org and proj and scfg.get("enabled")):
        res["error"] = "salad 未启用/未配置 key"
        return res
    base = str(scfg.get("base_url", "https://api.salad.com/api/public")).rstrip("/")
    pre = f"{base}/organizations/{org}/projects/{proj}/containers"
    names = scfg.get("include_container_groups") or []
    try:
        gp = salad_gpu_prices(base, org, key)
        if not names:
            d = salad_get(pre, key)
            names = [g.get("name") for g in (d.get("items") or [])]
        watch = read_state(account_id).get("salad_instance_watch") or {}
        # 矿池侧: salad worker 名 = <prefix>-salad-<machine_id>, gpu_info 带真实卡型
        pool = pool_data()
        pool_workers = (pool.get("connected_workers") or []) if isinstance(pool, dict) else []
        def pool_match(mid):
            if not mid:
                return None
            for w in pool_workers:
                if mid in str(w.get("worker_name") or ""):
                    return w
            return None
        def pool_worker(name):
            for w in pool_workers:
                if str(w.get("worker_name") or "") == str(name):
                    return w
            return None
        def pgpu(w):
            gi = (w or {}).get("gpu_info") or []
            return str((gi[0] if gi else {}).get("name") or "").replace("NVIDIA GeForce ", "").strip()
        def phr(w):
            gi = (w or {}).get("gpu_info") or []
            return round(sum(hashrate_th(g.get("hashrate")) for g in gi), 2) if gi else None
        def fetch_group(nm):  # 单组: 拉 组详情 + 实例; 返回片段, 由主线程按 names 顺序合并
            out = {"name": nm, "counts": None, "gpu_classes": [], "prices": [], "instances": [], "error": None}
            prio = "medium"
            label = None
            try:
                g = salad_get(f"{pre}/{urllib.parse.quote(str(nm))}", key)
                out["counts"] = (g.get("current_state") or {}).get("instance_status_counts") or {}
                prio = g.get("priority") or "medium"
                cls = ((g.get("container") or {}).get("resources") or {}).get("gpu_classes") or []
                for c in cls:
                    cname = gp.get(c, {}).get("name")
                    if cname:
                        out["gpu_classes"].append(cname)
                ps = [float(gp.get(c, {}).get("prices", {}).get(prio)) for c in cls
                      if gp.get(c, {}).get("prices", {}).get(prio) is not None]
                if ps:
                    lo, hi = min(ps), max(ps)
                    label = f"${lo:.3f}/h" if abs(lo - hi) < 1e-9 else f"${lo:.3f}–{hi:.3f}/h"
                    out["prices"] += ps
            except Exception:
                pass
            # 按组优先级建 GPU名→精确价 映射; 命中用单价, 否则兜底表, 再否则回退区间 label
            classprice = {}
            for info in gp.values():
                pr = info.get("prices", {}).get(prio)
                if info.get("name") and pr is not None:
                    classprice[gpu_key(info.get("name"))] = float(pr)
            def inst_price_num(gname):
                k = gpu_key(gname)
                if k in classprice:
                    return classprice[k]
                fb = SALAD_GPU_PRICES.get(k, {}).get(prio)
                return float(fb) if fb is not None else None
            def inst_price(gname):
                n = inst_price_num(gname)
                return f"${n:.3f}/h" if n is not None else label
            insts = []
            try:
                d = salad_get(f"{pre}/{urllib.parse.quote(str(nm))}/instances", key)
                insts = d.get("instances") or []
            except Exception as ie:
                out["error"] = f"instances: {type(ie).__name__}: {ie}"
            for inst in insts:
                iid = str(inst.get("instance_id") or inst.get("id") or "")
                mid = str(inst.get("machine_id") or "")
                w = watch.get(f"{nm}:{iid}") or {}
                pw = pool_match(mid) or pool_worker(nm)
                gpu = pgpu(pw) or (w.get("gpu") or "").strip() or "?"
                hr = w.get("last_hashrate_th")
                if hr is None:
                    hr = phr(pw)
                out["instances"].append({"id": iid, "machine_id": mid, "gpu": gpu, "group": nm,
                                         "state": inst.get("state"),
                                         "started_epoch": iso_to_epoch(inst.get("update_time")),
                                         "price": inst_price_num(gpu),
                                         "price_label": inst_price(gpu), "hashrate_th": hr})
            if not insts:  # /instances 失败/为空时, 用矿池 salad worker 兜底显示
                for w in pool_workers:
                    wn = str(w.get("worker_name") or "")
                    if "salad" not in wn:
                        continue
                    mm = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", wn)
                    mid = mm.group(0) if mm else wn
                    igpu = pgpu(w) or "?"
                    out["instances"].append({"id": mid, "machine_id": mid, "gpu": igpu,
                                             "group": nm, "state": "running", "started_epoch": None,
                                             "price": inst_price_num(igpu),
                                             "price_label": inst_price(igpu), "hashrate_th": phr(w)})
            return out
        if names:
            with ThreadPoolExecutor(max_workers=min(SALAD_WORKERS, len(names))) as ex:
                group_results = list(ex.map(fetch_group, names))  # map 保序 → 合并顺序同原串行
        else:
            group_results = []
        prices = []
        for gr in group_results:
            if gr["counts"] is not None:
                res["counts"][gr["name"]] = gr["counts"]
            for cn in gr["gpu_classes"]:
                if cn not in res["gpu_classes"]:
                    res["gpu_classes"].append(cn)
            res["instances"].extend(gr["instances"])
            prices += gr["prices"]
            if gr["error"] and not res["error"]:
                res["error"] = gr["error"]
        if prices:
            lo, hi = min(prices), max(prices)
            res["price_label"] = f"${lo:.3f}/h" if abs(lo - hi) < 1e-9 else f"${lo:.3f}–{hi:.3f}/h"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res

def _http_json(method, url, headers, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = dict(headers); h.setdefault("User-Agent", "sniper-dashboard/1.0")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

_bal = {}  # plat -> (value|None, ts)
BAL_STALE_MAX = 300.0  # serve-stale: 后台刷新余额; 超此值才在请求线程兜底重拉(余额变动慢, 上限放宽)

def estimate_manual_balance(balance_usd, asof_epoch, burn_hourly, now):
    """从手填余额按 burn rate 自动递减的估算当前余额(USD, 不为负)。
    Salad/TensorDock 无余额 API: 用户在 config 填一次 balance_usd(+自动记录 balance_asof),
    看板据当前消耗速率估算 now 时刻余额。不知道充值/精确计费, 会逐渐偏差, 需偶尔回填校准。"""
    elapsed_h = max(0.0, (now - asof_epoch) / 3600.0)
    return round(max(0.0, float(balance_usd) - float(burn_hourly) * elapsed_h), 2)

def platform_balance(account_id, force=False):
    """账户余额(USD)。Vast=credit, RunPod=clientBalance; TensorDock/Salad 无可用 API → None(可手填估算)。
    serve-stale: 请求线程读缓存不阻塞, 后台 force 刷新。"""
    now = time.time()
    c = _bal.get(account_id)
    if c and not force and (now - c[1] < BAL_STALE_MAX):
        return c[0]
    val = None
    plat = platform_of(account_id)
    env = read_env()
    kv = key_var_for(account_id)
    try:
        if plat == "vast":
            k = env.get(kv) or os.environ.get(kv, "")
            if k:
                d = _http_json("GET", "https://console.vast.ai/api/v0/users/current/", {"Authorization": "Bearer " + k})
                val = d.get("credit")
        elif plat == "runpod":
            k = env.get(kv) or os.environ.get(kv, "")
            if k:
                d = _http_json("POST", "https://api.runpod.io/graphql",
                               {"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                               body={"query": "query{myself{clientBalance}}"})
                val = ((d.get("data") or {}).get("myself") or {}).get("clientBalance")
        if val is not None:
            val = round(float(val), 2)
    except Exception:
        val = None
    _bal[account_id] = (val, now)
    return val

def active_rentals(account_id):
    st = read_state(account_id)
    out = []
    if platform_of(account_id) == "salad":
        for i in salad_live(account_id).get("instances", []):
            out.append({"id": i["id"], "gpu": i.get("gpu") or "?", "price": i.get("price"),
                        "price_label": i.get("price_label"),
                        "hashrate_th": i.get("hashrate_th"),
                        "created_epoch": i.get("started_epoch"),
                        "state": i.get("state"),  # running/creating/downloading… 仅 running 才算"在跑"
                        "group": i.get("group")})
        return out
    for r in st.get("rented", []):
        if not r.get("active"):
            continue
        out.append({"id": r.get("contract_id") or r.get("external_id"), "gpu": r.get("gpu"),
                    "price": r.get("price"), "hashrate_th": r.get("last_hashrate_th"),
                    "created_epoch": r.get("created_epoch")})
    return out


# ---------- 累计租金 ----------
def tick_spend():
    with _lock:
        s = read_json(STATS_PATH, {"cumulative_usd": 0.0, "last_epoch": time.time()})
        now = time.time()
        hourly = 0.0
        for plat in list_accounts():
            for r in active_rentals(plat):
                try:
                    hourly += float(r.get("price") or 0)
                except Exception:
                    pass
        dt = max(0.0, now - float(s.get("last_epoch", now)))
        if dt < 3600:
            s["cumulative_usd"] = float(s.get("cumulative_usd", 0.0)) + hourly * dt / 3600.0
        s["last_epoch"] = now
        s["current_hourly_usd"] = hourly
        try:
            json.dump(s, open(STATS_PATH, "w"))
        except Exception:
            pass
        return s

def spend_loop():
    while True:
        try:
            tick_spend()
        except Exception:
            pass
        try:
            tick_output()
        except Exception:
            pass
        time.sleep(60)


def _refresh_once():
    """后台预热所有缓存一轮: 矿池 + 各账号 salad 实时 + 余额。让 HTTP 请求只读缓存、永不阻塞。"""
    try:
        fetch_coin_price(force=True)
    except Exception:
        pass
    pool_data(force=True)
    try:
        twpool_data(force=True)
    except Exception:
        pass
    for acct in list_accounts():
        try:
            if platform_of(acct) == "salad":
                salad_live(acct, force=True)
            platform_balance(acct, force=True)
        except Exception:
            pass

def _refresh_loop():
    while True:
        try:
            _refresh_once()
        except Exception:
            pass
        time.sleep(REFRESH_INTERVAL)


# ---------- 累计产出(自看板起算) ----------
# 产出 = 矿池 balance_transactions 里的正向 epoch credit(负向 Auto Payment 是提现, 不算产出)
# + 当前待结算 pending。首次观测时把已有 credit 标记为基线、记录起始 pending,
# 之后只累加新出现的 credit; 显示值 = 新增已结算 + 当前 pending - 起始 pending(从 0 起涨)。
def tick_output(pool=None):
    if pool is None:
        pool = pool_data()
    if not isinstance(pool, dict):
        return float(read_json(STATS_PATH, {}).get("cumulative_output", 0.0))
    pending = float((pool.get("pending_rewards") or {}).get("total_pending") or 0)
    credits = [(int(t.get("timestamp") or 0), float(t.get("amount") or 0))
               for t in (pool.get("balance_transactions") or [])
               if float(t.get("amount") or 0) > 0]
    with _lock:
        s = read_json(STATS_PATH, {"cumulative_usd": 0.0, "last_epoch": time.time()})
        if not s.get("output_init"):
            s["output_init"] = True
            s["output_last_credit_ts"] = max([ts for ts, _ in credits], default=0)
            s["output_start_pending"] = pending
            s["output_settled_acc"] = 0.0
        else:
            last = int(s.get("output_last_credit_ts") or 0)
            newmax = last
            for ts, amt in credits:
                if ts > last:
                    s["output_settled_acc"] = float(s.get("output_settled_acc") or 0.0) + amt
                    if ts > newmax:
                        newmax = ts
            s["output_last_credit_ts"] = newmax
        cumulative = (float(s.get("output_settled_acc") or 0.0) + pending
                      - float(s.get("output_start_pending") or 0.0))
        if cumulative < 0:
            cumulative = 0.0
        s["cumulative_output"] = cumulative
        try:
            json.dump(s, open(STATS_PATH, "w"))
        except Exception:
            pass
        return cumulative


# ---------- 实时币价(SafeTrade REST) / 重置统计 ----------
def fetch_coin_price(force=False):
    """从 SafeTrade 拉取 PRL/USDT 最新成交价(ticker.last)。
    serve-stale: 后台刷新; 超 PRICE_STALE_MAX 才在请求线程同步重拉。
    API 失败时 fallback 到缓存旧值, 再 fallback 到 COIN_PRICE_USD 默认值。"""
    now = time.time()
    cached = _price_cache.get("prl")
    if cached and not force and (now - cached[1] < PRICE_STALE_MAX):
        return cached[0]
    try:
        req = urllib.request.Request(_SAFETRADE_URL,
                                     headers={"User-Agent": "sniper-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        p = float(d["ticker"]["last"])
        _price_cache["prl"] = (p, now)
        return p
    except Exception:
        if cached:
            return cached[0]
        return COIN_PRICE_USD

def coin_price():
    return fetch_coin_price()

def kline_data(period=15, force=False):
    """拉取 SafeTrade PRL/USDT K线(serve-stale 缓存)。period: 15/60/240/1440(分钟)。
    返回 [[ts,open,high,low,close,volume], ...] 或旧缓存/空列表。"""
    now = time.time()
    cached = _kline_cache.get(period)
    ttl = KLINE_TTL.get(period, 60)
    if cached and not force and (now - cached[1] < ttl):
        return cached[0]
    limit = _KLINE_LIMITS.get(period, 200)
    time_from = int(now) - limit * period * 60
    url = f"{_KLINE_BASE}?period={period}&time_from={time_from}&time_to={int(now)}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, list):
            _kline_cache[period] = (data, now)
            return data
    except Exception:
        pass
    return cached[0] if cached else []

def reset_stats():
    """累计租金/产出/利润全部清零, 从现在重新起算; 保留已设的币价。"""
    with _lock:
        old = read_json(STATS_PATH, {})
        now = time.time()
        s = {"cumulative_usd": 0.0, "current_hourly_usd": 0.0,
             "last_epoch": now, "reset_epoch": now}
        if old.get("coin_price_usd") is not None:
            s["coin_price_usd"] = old["coin_price_usd"]
        # 不写 output_* → 下次 tick_output 自动以当前 pending 重新基线
        try:
            json.dump(s, open(STATS_PATH, "w"))
        except Exception:
            pass
    try:
        tick_output()   # 立刻重新基线产出, 卡片即时归零
    except Exception:
        pass
    return {"ok": True}


# ---------- 矿池视图映射 ----------
def _pearlhash_view():
    """pearlhash 矿池视图: {workers, total_hashrate_th, pool_balance, pool_error}。"""
    pool = pool_data()
    err = pool.get("_error") if isinstance(pool, dict) else None
    workers = pool.get("connected_workers", []) if isinstance(pool, dict) else []
    wlist, total = [], 0.0
    for w in workers:
        wth = sum(hashrate_th(g.get("hashrate")) for g in (w.get("gpu_info") or []))
        total += wth
        wlist.append({"name": w.get("worker_name"), "th": round(wth, 2), "ip": w.get("ip"),
                      "gpus": [g.get("name") for g in (w.get("gpu_info") or [])]})
    bal = pool.get("balance") if isinstance(pool, dict) else None
    return {"workers": wlist, "total_hashrate_th": round(total, 2),
            "pool_balance": (float(bal) if bal is not None else None), "pool_error": err}

def _twpool_view():
    """twpool 矿池视图: {workers, total_hashrate_th, pool_balance, pool_error}。"""
    data = twpool_data()
    err = data.get("_error") if isinstance(data, dict) else None
    reported = (data.get("reported") or {}) if isinstance(data, dict) else {}
    addr = prl_address() or ""
    prefix = addr + "."
    wlist, total = [], 0.0
    for key, info in reported.items():
        worker = key[len(prefix):] if key.startswith(prefix) else key
        try:
            th = round(float((info or {}).get("hs") or 0) / 1e12, 2)
        except (TypeError, ValueError):
            th = 0.0
        total += th
        wlist.append({"name": worker, "th": th, "ip": None, "gpus": []})
    bal = data.get("balance") if isinstance(data, dict) else None
    return {"workers": wlist, "total_hashrate_th": round(total, 2),
            "pool_balance": (float(bal) if bal is not None else None), "pool_error": err}

def pool_view(which):
    """按 which 返回显示映射: {workers, total_hashrate_th, pool_balance, pool_error}。
    which: 'pearlhash' | 'twpool' | 'merged'(默认/未知 → merged)。无状态(产出在 build_summary 另算)。"""
    if which == "pearlhash":
        return _pearlhash_view()
    if which == "twpool":
        return _twpool_view()
    ph, tw = _pearlhash_view(), _twpool_view()
    by_name = {}
    for w in ph["workers"] + tw["workers"]:
        cur = by_name.get(w["name"])
        if cur is None or (w.get("th") or 0) > (cur.get("th") or 0):
            by_name[w["name"]] = w
    bals = [v for v in (ph["pool_balance"], tw["pool_balance"]) if v is not None]
    return {"workers": sorted(by_name.values(), key=lambda w: w.get("name") or ""),
            "total_hashrate_th": round((ph["total_hashrate_th"] or 0) + (tw["total_hashrate_th"] or 0), 2),
            "pool_balance": (round(sum(bals), 4) if bals else None),
            "pool_error": ph["pool_error"] or tw["pool_error"]}


# ---------- 总览数据 ----------
def _is_running(machine):
    """是否算"在跑": 非 salad 的活跃租约无 state(None)直接算; salad 按实例 state,
    仅 'running' 算(排除 creating/downloading/allocating/stopping —— 这些已分配但还没在挖)。"""
    return machine.get("state") in (None, "running")

def build_summary(pool_key="merged"):
    pool_key = pool_key if pool_key in ("pearlhash", "twpool", "merged") else "merged"
    pv = pool_view(pool_key)
    per_plat, running = {}, 0
    for acct in list_accounts():
        n = sum(1 for m in active_rentals(acct) if _is_running(m))  # 只数真正在跑(salad 排除创建/下载中)
        plat = platform_of(acct)
        per_plat[plat] = per_plat.get(plat, 0) + n
        running += n
    stats = read_json(STATS_PATH, {})
    cp = coin_price()
    rent_usd = round(float(stats.get("cumulative_usd", 0.0)), 4)
    ph_output = round(tick_output(pool_data()), 4)     # 始终调: 保持 pearlhash 自重置累加
    tw = twpool_data()
    tw_total = round(float((tw or {}).get("balance") or 0) + float((tw or {}).get("paid") or 0), 4) if isinstance(tw, dict) else 0.0
    if pool_key == "pearlhash":
        output, basis = ph_output, "since_reset"
    elif pool_key == "twpool":
        output, basis = tw_total, "all_time"
    else:
        output, basis = round(ph_output + tw_total, 4), "mixed"
    output_usd = round(output * cp, 2)       # 折合 USD
    return {
        "wallet": prl_address(),
        "running_machines": running,
        "running_by_platform": per_plat,
        "total_hashrate_th": pv["total_hashrate_th"],
        "workers": pv["workers"],
        "cumulative_rent_usd": rent_usd,
        "current_hourly_usd": round(float(stats.get("current_hourly_usd", 0.0)), 4),
        "coin_price_usd": cp,
        "coin_price_live": _price_cache.get("prl") is not None,  # True=实时拉取, False=fallback
        "cumulative_output": output,
        "cumulative_output_usd": output_usd,
        "cumulative_profit_usd": round(output_usd - rent_usd, 2),
        "produced_basis": basis,
        "pool_balance": pv["pool_balance"],
        "pool_view": pool_key,
        "stats_since": int(float(stats.get("reset_epoch") or stats.get("last_epoch") or 0)),
        "pool_error": pv["pool_error"],
        "ts": int(time.time()),
    }

def build_rentals():
    now = time.time()
    res = {}
    for acct in list_accounts():
        plat = platform_of(acct)
        cfg = read_config(acct).get(plat, {})
        items = []
        for r in active_rentals(acct):
            dur = int(now - float(r["created_epoch"])) if r.get("created_epoch") else None
            d = dict(r)
            d["duration_seconds"] = dur
            items.append(d)
        res[acct] = {
            "platform": plat,
            "account_id": acct,
            "label": account_label(acct),
            "enabled": cfg.get("enabled"),
            "create_enabled": cfg.get("create_enabled"),
            "rent_paused": rent_paused(acct),
            "process_running": pid_for(acct) is not None,
            "thresholds": cfg.get("thresholds"),
            "min_hashrate_th": cfg.get("min_hashrate_th"),
            "machines": items,
        }
        bal = platform_balance(acct)
        burn = sum(float(m.get("price") or 0) for m in items)
        estimated = False
        if bal is None and cfg.get("balance_usd") is not None:  # 无 API 余额时用手填值按消耗估算
            try:
                asof = iso_to_epoch(cfg.get("balance_asof")) or now
                bal = estimate_manual_balance(cfg.get("balance_usd"), asof, burn, now)
                estimated = True
            except Exception:
                bal = None
        res[acct]["balance"] = bal
        res[acct]["balance_estimated"] = estimated
        res[acct]["balance_editable"] = plat in NO_BALANCE_API  # 无 API 的平台允许总览内联手填
        res[acct]["balance_usd"] = cfg.get("balance_usd")        # 原始手填值, 供编辑框预填
        res[acct]["burn_hourly"] = round(burn, 4)
        res[acct]["hours_left"] = round(bal / burn, 1) if (bal is not None and burn > 0) else None
        if plat == "salad":
            sl = salad_live(acct)
            cnts = {}
            for c in (sl.get("counts") or {}).values():
                for k, v in (c or {}).items():
                    cnts[k] = cnts.get(k, 0) + (v or 0)
            res[acct]["salad_status"] = cnts
            res[acct]["salad_error"] = sl.get("error")
            res[acct]["salad_gpu_classes"] = sl.get("gpu_classes") or []
    return res

def build_config():
    env = read_env()
    res = {}
    for acct in list_accounts():
        kn = key_var_for(acct)
        v = env.get(kn, "")
        is_set = bool(v) and not v.startswith("replace_with")
        res[acct] = {
            "platform": platform_of(acct),
            "label": account_label(acct),
            "key_name": kn,
            "key_set": is_set,
            "key_mask": ("…" + v[-4:]) if (is_set and len(v) >= 4) else ("已设置" if is_set else ""),
            "process_running": pid_for(acct) is not None,
            "rent_paused": rent_paused(acct),
        }
    return res


# ---------- 配置编辑 ----------
def gpu_rows(sub):
    th = sub.get("thresholds") or {}
    mh = sub.get("min_hashrate_th") or {}
    # 按"去掉冗余 GeForce 前缀"后的短名归一去重: 即使某型号只有全称 key
    # (如 NVIDIA GeForce RTX 5090) 也能展示, 保存时不会被空表覆盖丢失(P1-A)。
    rows = {}
    for k in list(th) + list(mh):
        short = k.replace("NVIDIA GeForce ", "").strip()
        norm = short.upper()
        r = rows.setdefault(norm, {"gpu": short, "max_price": None, "min_hashrate": None})
        if "GeForce" not in k:
            r["gpu"] = short  # 展示优先用不带 GeForce 的短名
        if r["max_price"] is None and th.get(k) is not None:
            r["max_price"] = th.get(k)
        if r["min_hashrate"] is None and mh.get(k) is not None:
            r["min_hashrate"] = mh.get(k)
    return list(rows.values())

def build_full_config():
    env = read_env()
    accts = list_accounts()
    import sniper as S
    all_cfgs = {acct: read_config(acct) for acct in accts}
    base = all_cfgs[accts[0]] if accts else {}
    common = {k: base.get(k) for k in COMMON_KEYS}
    # P2-E: 各账号 common 字段是否有分歧(供前端提示 + 保存覆盖确认)
    common_diff = {}
    for k in COMMON_KEYS:
        vals = {acct: all_cfgs[acct].get(k) for acct in accts}
        if len({json.dumps(v, ensure_ascii=False, sort_keys=True) for v in vals.values()}) > 1:
            common_diff[k] = vals
    plats = {}
    for acct in accts:
        plat = platform_of(acct)
        cfg = all_cfgs[acct]
        sub = cfg.get(plat, {}) or {}
        kn = key_var_for(acct)
        v = env.get(kn, "")
        is_set = bool(v) and not v.startswith("replace_with")
        spec = []
        for key, typ in SPECIFIC[plat]:
            spec.append({"key": key, "type": typ, "value": sub.get(key)})
        plats[acct] = {
            "platform": plat,
            "label": account_label(acct),
            "enabled": bool(sub.get("enabled")),
            "has_create": plat in HAS_CREATE,
            "create_enabled": bool(sub.get("create_enabled")),
            "min_th_per_usd_hour": sub.get("min_th_per_usd_hour"),
            "gpus": gpu_rows(sub),
            "specific": spec,
            "key_name": kn,
            "key_set": is_set,
            "key_mask": ("…" + v[-4:]) if (is_set and len(v) >= 4) else "",
            "process_running": pid_for(acct) is not None,
            "rent_paused": rent_paused(acct),
            "raw": json.dumps(cfg, ensure_ascii=False, indent=2),
            "pool": S.active_pool(cfg),
        }
    return {"common": common, "common_diff": common_diff, "platforms": plats,
            "pools": [{"id": k, "label": v["label"]} for k, v in S.POOLS.items()]}

def backup_and_write(path, obj):
    try:
        if path.exists():
            (path.parent / (path.name + ".bak")).write_text(path.read_text())
    except Exception:
        pass
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

def save_platform_cfg(acct, patch):
    if acct not in list_accounts() or not isinstance(patch, dict):
        return {"error": "参数无效"}
    plat = platform_of(acct)
    p = cfg_path(acct)
    cfg = read_json(p, {})
    sub = cfg.get(plat, {}) or {}
    if "balance_usd" in patch:  # 手填余额变化时自动记录时间, 供看板按消耗递减估算
        try:
            old = sub.get("balance_usd")
            if patch["balance_usd"] is not None and (old is None or float(old) != float(patch["balance_usd"])):
                patch = dict(patch)
                patch["balance_asof"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        except Exception:
            pass
    sub.update(patch)
    cfg[plat] = sub
    backup_and_write(p, cfg)
    return {"ok": True, "platform": acct}

def save_pool_cfg(acct, pool):
    """切换某账号'新抢机器用哪个矿池'(顶层 config['pool'], 只影响新 create, 不迁移老机器)。"""
    import sniper as S
    if acct not in list_accounts():
        return {"error": "账号无效"}
    pool = str(pool or "").strip()
    if pool not in S.POOLS:
        return {"error": f"未知矿池: {pool}"}
    p = cfg_path(acct)
    cfg = read_json(p, {})
    cfg["pool"] = pool          # 顶层! 不是 cfg[plat]
    backup_and_write(p, cfg)
    return {"ok": True, "platform": acct, "pool": pool}

def save_common_cfg(data):
    if not isinstance(data, dict):
        return {"error": "参数无效"}
    data = {k: v for k, v in data.items() if k in COMMON_KEYS}
    accts = list_accounts()
    for acct in accts:
        p = cfg_path(acct)
        cfg = read_json(p, {})
        cfg.update(data)
        backup_and_write(p, cfg)
    return {"ok": True, "written": len(accts)}

def save_raw_cfg(acct, raw):
    if acct not in list_accounts():
        return {"error": "账号无效"}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"error": "顶层必须是 JSON 对象"}
    except Exception as e:
        return {"error": f"JSON 非法: {e}"}
    backup_and_write(cfg_path(acct), obj)
    return {"ok": True, "platform": acct}

def launch_platform(acct):
    env = dict(os.environ)
    for k, v in read_env().items():
        env[k] = v
    # 多账号: 把该账号的 key 注入成 sniper 期望的标准变量名(account2 的 key 在 *_2)
    kv = read_env().get(key_var_for(acct), "")
    std = KEYNAME.get(platform_of(acct), "")
    if kv and std:
        env[std] = kv
    env["SNIPER_LOG_PATH"] = f"logs/{acct}.log"
    env["SNIPER_STATE_PATH"] = f"state.{acct}.json"
    try:
        logf = open(ROOT / f"logs/{acct}.log", "a")
        subprocess.Popen(["python3", "sniper.py", "--config", f"configs/config.{acct}.json", "--live"],
                         cwd=str(ROOT), env=env, stdout=logf, stderr=logf, start_new_session=True)
        return True
    except Exception:
        return False

def restart_platform(acct):
    if acct not in list_accounts():
        return {"error": "账号无效"}
    pid = pid_for(acct)
    if pid:
        try:
            subprocess.run(["kill", pid])
        except Exception:
            pass
        time.sleep(2)
    ok = launch_platform(acct)
    time.sleep(1.5)
    return {"ok": ok, "platform": acct, "process_running": pid_for(acct) is not None}

def do_rent_toggle(acct, paused):
    CONTROL_DIR.mkdir(exist_ok=True)
    # sniper 按平台名读 control/<平台>.rent-paused, 故暂停是平台级(同平台账号联动)
    flag = CONTROL_DIR / f"{platform_of(acct)}.rent-paused"
    launched = False
    if paused:
        flag.touch()
    else:
        if flag.exists():
            flag.unlink()
        if pid_for(acct) is None:
            launched = launch_platform(acct)
            time.sleep(1.5)
    return {"ok": True, "platform": acct, "rent_paused": flag.exists(),
            "process_running": pid_for(acct) is not None, "launched": launched}

def do_terminate(acct, mid, group=None):
    if not mid:
        return {"error": "缺少实例 id"}
    try:
        import sniper as S
        plat = platform_of(acct)
        cfg = read_config(acct)
        # 注入该账号 key, 让销毁/reallocate 用对账号(account2 的 key 在 *_2)
        kv = read_env().get(key_var_for(acct), "")
        std = KEYNAME.get(plat, "")
        if kv and std:
            os.environ[std] = kv
        if plat == "vast":
            r = S.destroy_vast_instance(mid)
        elif plat == "runpod":
            r = S.delete_runpod_pod(mid)
        elif plat == "tensordock":
            r = S.delete_tensordock_instance(cfg, mid)
        elif plat == "salad":
            r = S.reallocate_salad_instance(cfg, group or "", mid)
        else:
            return {"error": "平台无效"}
        return {"ok": True, "platform": acct, "id": mid, "result": r}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def do_migrate(data):
    """一键迁移到 target_pool。confirm 必须 == 'MIGRATE'。platform 为账号 id 或 'all'。
    逐账号: 注入该账号 key → 持久化 pool(save_pool_cfg)→ 调 sniper.migrate_account 实际迁移现有机器。
    state 传 {}(运行中的监控自管 state, 双池监控保护迁移机器, 避免写 state 竞争)。
    注: 新抢机器用新池需重启该账号监控才生效(migrate 只改现有机器 + 落盘 pool)。"""
    import sniper as S
    if str(data.get("confirm", "")) != "MIGRATE":
        return {"error": "需输入确认词 MIGRATE"}
    target = str(data.get("target_pool", "")).strip()
    if target not in S.POOLS:
        return {"error": f"未知矿池: {target}"}
    platform = str(data.get("platform", ""))
    if platform == "all":
        accts = list_accounts()
    elif platform in list_accounts():
        accts = [platform]
    else:
        return {"error": "账号无效"}
    results = []
    for acct in accts:
        try:
            plat = platform_of(acct)
            kv = read_env().get(key_var_for(acct), "")
            std = KEYNAME.get(plat, "")
            if kv and std:
                os.environ[std] = kv
            sp = save_pool_cfg(acct, target)            # 先落盘 pool
            if isinstance(sp, dict) and sp.get("error"):
                # 落盘失败则不迁移该账号(避免配置与实际不一致), 但不中断其它账号
                results.append({"account": acct, "result": {"error": f"落盘 pool 失败: {sp.get('error')}"}})
                continue
            # vast 迁移特殊: 镜像创建时烧死, 迁移=销毁重租, 靠监控用新池镜像重租。
            # 监控只在启动时读 config → 必须先重启监控加载新池, 否则销毁后用旧池镜像重租(白干)。
            # 重启成功才销毁; 监控没起来则取消(避免销毁后无监控重租导致机器丢失)。runpod/salad 直接换镜像, 无需重启。
            restarted = False
            if plat == "vast":
                rp = restart_platform(acct)
                if not (isinstance(rp, dict) and rp.get("process_running")):
                    results.append({"account": acct, "result": {"error": "vast 监控重启失败, 已取消迁移(避免销毁后无监控重租)"}})
                    continue
                restarted = True
            cfg = read_config(acct)
            r = S.migrate_account(cfg, {}, acct, target, live=True)
            if isinstance(r, dict):
                r["monitor_restarted"] = restarted
        except Exception as e:
            r = {"error": f"{type(e).__name__}: {e}"}
        results.append({"account": acct, "result": r})
    return {"ok": True, "target_pool": target, "accounts": results}


# ---------- HTTP ----------
def _secret():
    return hashlib.sha256(("pearl-dash::" + str(CONF.get("password", ""))).encode()).digest()

def new_session(role="admin"):
    exp = str(int(time.time() + SESS_TTL))
    payload = f"{role}.{exp}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def session_role(token):
    """返回 'admin' / 'guest'(访客);无效或过期返回 None。"""
    try:
        role, exp, sig = str(token).split(".", 2)
        good = hmac.new(_secret(), f"{role}.{exp}".encode(), hashlib.sha256).hexdigest()
        if role in ("admin", "guest") and hmac.compare_digest(sig, good) and time.time() < float(exp):
            return role
    except Exception:
        pass
    return None

class H(BaseHTTPRequestHandler):
    server_version = "pearl-dash"

    def log_message(self, *a):
        pass

    def _cookie_token(self):
        c = self.headers.get("Cookie", "")
        for part in c.split(";"):
            part = part.strip()
            if part.startswith("sniper_session="):
                return part.split("=", 1)[1]
        return ""

    def _role(self):
        return session_role(self._cookie_token())

    def _authed(self):
        return self._role() is not None

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._send(200, HTML, "text/html")
        if path.startswith("/api/"):
            role = self._role()
            if not role:
                return self._send(401, {"error": "unauthorized"})
            if path == "/api/me":
                return self._send(200, {"role": role, "user": "admin" if role == "admin" else "访客"})
            if path == "/api/kline":
                try:
                    qs = urllib.parse.parse_qs(self.path.split("?",1)[1] if "?" in self.path else "")
                    p = int((qs.get("period") or ["15"])[0])
                    if p not in (15, 60, 240, 1440): p = 15
                except Exception:
                    p = 15
                return self._send(200, kline_data(p))
            if path == "/api/summary":
                qs = urllib.parse.parse_qs(self.path.split("?",1)[1] if "?" in self.path else "")
                pk = (qs.get("pool") or ["merged"])[0]
                return self._send(200, build_summary(pk))
            if path == "/api/rentals":
                return self._send(200, build_rentals())
            # ↓ 以下仅管理员;访客(guest)只能看总览数据与工具集
            if role != "admin":
                return self._send(403, {"error": "forbidden"})
            if path == "/api/config":
                return self._send(200, build_config())
            if path == "/api/full-config":
                return self._send(200, build_full_config())
            if path == "/api/logs":
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                plat = (q.get("platform") or [""])[0]
                n = (q.get("lines") or ["300"])[0]
                if plat not in list_accounts():
                    return self._send(400, {"error": "账号无效"})
                return self._send(200, {"platform": plat, "log": tail_log(plat, n)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        data = self._body_json()
        if path == "/login":
            if data.get("guest"):
                tok = new_session("guest")
                return self._send(200, {"ok": True, "role": "guest"}, extra={
                    "Set-Cookie": f"sniper_session={tok}; Path=/; Max-Age={SESS_TTL}; HttpOnly; SameSite=Lax"})
            ok = hmac.compare_digest(str(data.get("password", "")), str(CONF.get("password", "")))
            if not ok:
                return self._send(401, {"error": "密码错误"})
            tok = new_session("admin")
            return self._send(200, {"ok": True, "role": "admin"}, extra={
                "Set-Cookie": f"sniper_session={tok}; Path=/; Max-Age={SESS_TTL}; HttpOnly; SameSite=Lax"})
        if path == "/logout":
            return self._send(200, {"ok": True}, extra={
                "Set-Cookie": "sniper_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
        # 以下写操作仅管理员;访客一律拒绝
        if self._role() != "admin":
            return self._send(401 if not self._authed() else 403, {"error": "forbidden"})
        if path == "/api/key":
            plat = str(data.get("platform", ""))
            value = str(data.get("value", "")).strip()
            if plat not in list_accounts() or not value:
                return self._send(400, {"error": "参数无效"})
            set_env_key(key_var_for(plat), value)
            return self._send(200, {"ok": True, "platform": plat})
        if path == "/api/rent-toggle":
            plat = str(data.get("platform", ""))
            if plat not in list_accounts():
                return self._send(400, {"error": "账号无效"})
            return self._send(200, do_rent_toggle(plat, bool(data.get("paused"))))
        if path == "/api/terminate":
            plat = str(data.get("platform", ""))
            if plat not in list_accounts():
                return self._send(400, {"error": "账号无效"})
            return self._send(200, do_terminate(plat, str(data.get("id", "")), str(data.get("group", "")) or None))
        if path == "/api/migrate":
            return self._send(200, do_migrate(data))
        if path == "/api/set-pool":
            return self._send(200, save_pool_cfg(str(data.get("platform", "")), data.get("pool")))
        if path == "/api/save-platform":
            return self._send(200, save_platform_cfg(str(data.get("platform", "")), data.get("data")))
        if path == "/api/save-common":
            return self._send(200, save_common_cfg(data.get("data")))
        if path == "/api/save-raw":
            return self._send(200, save_raw_cfg(str(data.get("platform", "")), str(data.get("json", ""))))
        if path == "/api/restart":
            return self._send(200, restart_platform(str(data.get("platform", ""))))
        if path == "/api/dashboard-password":
            return self._send(200, set_dashboard_password(data.get("password", "")))
        if path == "/api/reset-stats":
            return self._send(200, reset_stats())
        return self._send(404, {"error": "not found"})


HTML = r"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>今晚挖珍珠 · Pearl Sniper</title>
<meta name=theme-color content="#f3f6fc">
<link rel="icon" href="data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2064%2064'><defs><radialGradient%20id='g'%20cx='37%25'%20cy='31%25'%20r='78%25'><stop%20offset='0%25'%20stop-color='%23f2fffc'/><stop%20offset='17%25'%20stop-color='%238ff3e6'/><stop%20offset='42%25'%20stop-color='%233fe0c5'/><stop%20offset='71%25'%20stop-color='%231aa6cf'/><stop%20offset='100%25'%20stop-color='%23083f57'/></radialGradient><radialGradient%20id='h'%20cx='50%25'%20cy='50%25'%20r='50%25'><stop%20offset='0%25'%20stop-color='%233fe0c5'%20stop-opacity='0.55'/><stop%20offset='100%25'%20stop-color='%233fe0c5'%20stop-opacity='0'/></radialGradient></defs><circle%20cx='32'%20cy='32'%20r='27'%20fill='url(%23h)'/><circle%20cx='32'%20cy='32'%20r='17'%20fill='url(%23g)'/><ellipse%20cx='25.5'%20cy='24'%20rx='6.5'%20ry='4.6'%20fill='%23ffffff'%20opacity='0.92'/></svg>">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Roboto+Mono:wght@400;500;600;700&display=swap');
:root{--bg:#0a0e17;--bg2:#0f1623;--card:#141b2b;--card2:#172033;--bd:#23304a;--bd2:#33415f;
--tx:#c4cde2;--hi:#eef2fb;--mut:#79839c;--g1:#5a8dff;--g2:#3fe0c5;
--acc:#3fe0c5;--acc2:rgba(63,224,197,.12);--ok:#3fe0c5;--okbg:rgba(63,224,197,.12);
--warn:#ffb259;--warnbg:rgba(255,178,89,.13);--bad:#ff7a7a;--badbg:rgba(255,122,122,.13);
--mono:'Roboto Mono',ui-monospace,"SF Mono",Menlo,monospace}
:root[data-theme=light]{--bg:#f3f6fc;--bg2:#e9eef7;--card:#ffffff;--card2:#f8fafd;--bd:#dde4f0;--bd2:#c9d3e3;
--tx:#3c4660;--hi:#101a2c;--mut:#74829a;--g1:#3a6cf0;--g2:#0fae93;
--acc:#0b9a82;--acc2:rgba(15,174,147,.12);--ok:#0b9a82;--okbg:rgba(15,174,147,.12);
--warn:#b9740f;--warnbg:rgba(214,142,30,.15);--bad:#d6453f;--badbg:rgba(214,69,63,.1)}
:root[data-theme=light] body{background:radial-gradient(1100px 460px at 50% -260px,rgba(58,108,240,.08),transparent 70%),radial-gradient(900px 400px at 90% -200px,rgba(15,174,147,.06),transparent 70%),var(--bg)}
:root[data-theme=light] header{background:rgba(255,255,255,.8)}
:root[data-theme=light] .side{background:rgba(255,255,255,.62)}
:root[data-theme=light] input,:root[data-theme=light] textarea{background:#fff;color:var(--hi)}
:root[data-theme=light] select{background:#fff;color:var(--tx)}
:root[data-theme=light] .logbox{background:#f1f4fa;color:#46546b}
:root[data-theme=light] .card,:root[data-theme=light] .platbox,:root[data-theme=light] .lcard{box-shadow:0 8px 24px -18px rgba(20,40,80,.28)}
:root[data-theme=light] table{box-shadow:0 8px 24px -20px rgba(20,40,80,.22)}
*{box-sizing:border-box}html,body{margin:0}
body{color:var(--tx);font-size:13.5px;
font-family:'Inter',-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
background:radial-gradient(1100px 460px at 50% -260px,rgba(90,141,255,.12),transparent 70%),radial-gradient(900px 400px at 90% -200px,rgba(63,224,197,.07),transparent 70%),var(--bg)}
::selection{background:var(--g2);color:#06121a}
.mono,.card .v,.wallet .addr,.clock,td{font-family:var(--mono);font-feature-settings:"tnum"}
header{background:rgba(15,22,35,.72);backdrop-filter:blur(10px);border-bottom:1px solid var(--bd);padding:0 24px;height:56px;display:flex;align-items:center;gap:22px;position:sticky;top:0;z-index:5}
.brand{font-weight:800;letter-spacing:.3px;font-size:15px;background:linear-gradient(92deg,var(--g1),var(--g2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.brand .v{font-weight:400;-webkit-text-fill-color:var(--mut)}
.tabs{display:flex;gap:5px}
.tab{padding:7px 16px;border-radius:9px;cursor:pointer;color:var(--mut);font-weight:600;letter-spacing:.3px;border:1px solid transparent;transition:.16s}
.tab:hover{color:var(--hi);background:rgba(255,255,255,.04)}
.tab.on{background:linear-gradient(92deg,var(--g1),var(--g2));color:#06121a;border-color:transparent;font-weight:700;box-shadow:0 4px 16px -6px rgba(63,224,197,.5)}
.clock{margin-left:auto;color:var(--mut);font-size:12px}
.srow{display:flex;align-items:center;justify-content:space-between;gap:9px;margin-bottom:9px}
.sicons{display:flex;align-items:center;gap:9px;flex-shrink:0}
.tbtn,.ghlink{width:26px;height:26px;border-radius:7px;border:1px solid var(--bd);background:transparent;color:var(--mut);font-size:13px;line-height:1;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:.14s;opacity:.75;backdrop-filter:blur(4px);flex-shrink:0}
.tbtn:hover,.ghlink:hover{border-color:var(--g2);color:var(--acc);opacity:1;background:rgba(127,127,127,.08)}
.ghlink svg{display:block}
.wrap{max-width:1180px;margin:24px auto;padding:0 20px}
.lbl{color:var(--mut);font-size:11px;letter-spacing:1.3px;font-weight:600;text-transform:uppercase;margin:0 0 12px;display:flex;align-items:center}
.lbl:before{content:"";display:inline-block;width:16px;height:2px;border-radius:2px;margin-right:9px;background:linear-gradient(90deg,var(--g1),var(--g2))}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(184px,1fr));gap:13px}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--bd);border-radius:13px;padding:17px 19px;box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 14px 30px -22px rgba(0,0,0,.8)}
.card .k{color:var(--mut);font-size:11px;letter-spacing:.9px;margin-bottom:9px;text-transform:uppercase}
.card .v{font-size:26px;font-weight:700;letter-spacing:-.4px;color:var(--hi)}
.card .v small{font-size:12px;color:var(--mut);font-weight:400;font-family:'Inter'}
.card .sub{color:var(--mut);font-size:11px;margin-top:7px}
.wallet{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:22px}
.wallet .addr{font-size:15px;font-weight:600;word-break:break-all;line-height:1.55;color:var(--hi)}
.wallet .go{flex-shrink:0;background:var(--acc2);color:var(--acc);border:1px solid rgba(63,224,197,.4);border-radius:9px;padding:9px 15px;font-weight:700;white-space:nowrap;letter-spacing:.3px;cursor:pointer;transition:.14s}
.wallet .go:hover{background:rgba(63,224,197,.2);box-shadow:0 6px 18px -8px rgba(63,224,197,.5)}
.sec{margin-top:28px}
.kpanel{margin-top:14px;border:1px solid var(--bd);border-radius:13px;overflow:hidden;background:var(--card);transition:.2s}
.khead{display:flex;align-items:center;gap:10px;padding:10px 16px;cursor:pointer;user-select:none;border-bottom:1px solid transparent;transition:.16s}
.khead:hover{background:rgba(255,255,255,.03)}
.kpanel.open .khead{border-bottom-color:var(--bd)}
.ktit{font-size:12px;font-weight:600;letter-spacing:.5px;color:var(--mut)}
.kprice{font-family:var(--mono);font-size:13px;font-weight:700;color:var(--hi)}
.kdelta{font-size:11px;font-family:var(--mono)}
.kpers{display:flex;gap:4px;margin-left:auto}
.kper{padding:3px 9px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--bd);color:var(--mut);transition:.13s}
.kper:hover{color:var(--hi);border-color:var(--bd2)}
.kper.on{background:var(--acc2);color:var(--acc);border-color:rgba(63,224,197,.4)}
.karr{font-size:11px;color:var(--mut);margin-left:6px;transition:.25s}
.kpanel.open .karr{transform:rotate(180deg)}
.kbody{display:none;padding:14px 16px 10px}
.kpanel.open .kbody{display:block}
.kcanvas-wrap{position:relative;width:100%}
canvas.kc{width:100%;display:block;border-radius:8px}
.ktip{position:absolute;top:6px;left:12px;background:rgba(10,14,23,.88);border:1px solid var(--bd2);border-radius:8px;padding:6px 10px;font-size:11px;font-family:var(--mono);color:var(--hi);pointer-events:none;display:none;white-space:nowrap;z-index:10;line-height:1.7}
.kema-legend{display:flex;gap:14px;font-size:11px;font-family:var(--mono);margin-bottom:6px;color:var(--mut)}
.kema-legend span{display:flex;align-items:center;gap:5px}
.kema-legend i{display:inline-block;width:18px;height:2px;border-radius:1px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:13px;overflow:hidden;box-shadow:0 14px 30px -24px rgba(0,0,0,.8)}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--bd)}
th{color:var(--mut);font-weight:600;font-size:10.5px;letter-spacing:1px;text-transform:uppercase;background:rgba(255,255,255,.02);font-family:'Inter'}
tr:last-child td{border-bottom:none}td{font-size:12.5px}
.pill{display:inline-block;padding:3px 10px;border-radius:6px;font-size:10.5px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;border:1px solid transparent}
.ok{background:var(--okbg);color:var(--ok);border-color:rgba(63,224,197,.3)}.bad{background:var(--badbg);color:var(--bad);border-color:rgba(255,122,122,.3)}.warn{background:var(--warnbg);color:var(--warn);border-color:rgba(255,178,89,.3)}.mut{background:rgba(255,255,255,.05);color:var(--mut);border-color:var(--bd)}
.platbox{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--bd);border-radius:13px;padding:17px 19px;margin-bottom:15px;box-shadow:0 14px 30px -24px rgba(0,0,0,.8)}
.platbox .top{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.platbox .top b{font-size:14px;font-weight:700;letter-spacing:.8px;color:var(--hi)}
.bal{margin-left:auto;color:var(--mut);font-size:12px;white-space:nowrap;font-family:var(--mono)}
.bal.editable{cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:8px;border:1px solid transparent;transition:.15s}
.bal.editable:hover{color:var(--hi);background:var(--acc2);border-color:rgba(63,224,197,.32)}
.ed-pen{font-size:10.5px;opacity:.45;transition:.15s}
.bal.editable:hover .ed-pen{opacity:1;color:var(--acc)}
.bal-edit{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono)}
.bal-edit .cur{color:var(--mut);font-size:13px}
.bal-edit input{width:84px;background:var(--bg);border:1px solid var(--bd2);border-radius:8px;color:var(--hi);font-family:var(--mono);font-size:13px;padding:6px 9px;outline:none;text-align:right;-moz-appearance:textfield}
.bal-edit input::-webkit-outer-spin-button,.bal-edit input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.bal-edit input:focus{border-color:var(--acc);box-shadow:0 0 0 2px rgba(63,224,197,.16)}
.bal-edit .bb{border:1px solid var(--bd);border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:14px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;transition:.14s;background:var(--card)}
.bal-edit .bb.ok{background:var(--acc2);color:var(--acc);border-color:rgba(63,224,197,.42)}
.bal-edit .bb.ok:hover{background:rgba(63,224,197,.24)}
.bal-edit .bb.x{color:var(--mut)}
.bal-edit .bb.x:hover{color:var(--hi);background:rgba(255,255,255,.07)}
.req{color:var(--bad);font-size:10px;border:1px solid rgba(255,122,122,.4);background:var(--badbg);border-radius:5px;padding:1px 6px;letter-spacing:.5px}
.cdiff{color:var(--warn);font-size:10px;border:1px solid rgba(255,178,89,.4);background:var(--warnbg);border-radius:5px;padding:1px 6px;cursor:help}
button{font-family:inherit;border:1px solid var(--bd2);background:rgba(255,255,255,.04);color:var(--tx);border-radius:9px;padding:8px 14px;cursor:pointer;font-size:12px;font-weight:600;letter-spacing:.2px;transition:.14s}
button:hover{border-color:var(--g2);color:var(--hi);background:rgba(63,224,197,.06)}
.b-acc{background:linear-gradient(92deg,var(--g1),var(--g2));color:#06121a;border-color:transparent;box-shadow:0 6px 18px -8px rgba(63,224,197,.55)}.b-acc:hover{color:#06121a;filter:brightness(1.06)}
.peek{margin-top:13px;text-align:center;color:var(--mut);font-size:11.5px;letter-spacing:.4px;font-family:var(--mono);cursor:pointer;padding:8px;border-top:1px solid var(--bd);transition:.14s}
.peek:hover{color:var(--acc)}
.logout{margin-top:9px;color:var(--mut);font-size:11px;font-family:var(--mono);cursor:pointer;letter-spacing:.4px;display:inline-flex;align-items:center;gap:5px;transition:.14s}
.logout:hover{color:var(--bad)}
.b-warn{background:var(--warnbg);color:var(--warn);border-color:rgba(255,178,89,.4)}.b-warn:hover{color:var(--warn);border-color:var(--warn)}
.b-bad{background:var(--badbg);color:var(--bad);border-color:rgba(255,122,122,.35);padding:6px 12px;font-size:11.5px}.b-bad:hover{color:var(--bad);border-color:var(--bad)}
input,textarea{background:#0c1320;border:1px solid var(--bd2);color:var(--hi);border-radius:9px;padding:9px 11px;font-size:12px;font-family:inherit;width:100%;outline:none;transition:.14s}
input:focus,textarea:focus{border-color:var(--g2);box-shadow:0 0 0 3px rgba(63,224,197,.15)}
textarea{resize:vertical;min-height:150px;line-height:1.5;font-family:var(--mono)}
.row{display:flex;gap:9px;align-items:center}
.grid2{display:grid;grid-template-columns:160px 1fr;gap:10px 13px;align-items:center}
.fld{color:var(--mut);font-size:11.5px}
.gpurow{display:grid;grid-template-columns:1fr 110px 110px 34px;gap:8px;margin-bottom:8px}
.hint{color:var(--mut);font-size:11px;margin:4px 0 0}
details{margin-top:13px;border-top:1px solid var(--bd);padding-top:11px}
summary{cursor:pointer;color:var(--mut);font-size:11.5px;letter-spacing:.4px}
summary:hover{color:var(--acc)}
.divider{border:0;border-top:1px solid var(--bd);margin:11px 0}
.toast{position:fixed;bottom:22px;right:22px;background:var(--card);border:1px solid var(--g2);color:var(--acc);padding:12px 17px;border-radius:11px;font-size:12px;z-index:30;display:none;box-shadow:0 18px 50px -20px rgba(63,224,197,.4)}
#login{position:fixed;inset:0;background:radial-gradient(1000px 560px at 50% -120px,rgba(90,141,255,.18),transparent 60%),radial-gradient(800px 500px at 50% 100%,rgba(63,224,197,.1),transparent 60%),var(--bg);display:flex;align-items:center;justify-content:center;z-index:20}
#login .box{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--bd);border-radius:16px;padding:32px;width:344px;box-shadow:0 30px 70px -28px rgba(0,0,0,.85)}
#login .logo{font-size:12px;letter-spacing:1.5px;margin-bottom:14px;font-family:var(--mono);background:linear-gradient(92deg,var(--g1),var(--g2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
#login h2{margin:0 0 4px;font-size:25px;font-weight:800;color:var(--hi)}
#login .sub{color:var(--mut);font-size:12px;letter-spacing:1.5px;margin-bottom:20px}
.muted{color:var(--mut);font-size:11.5px}
.err{color:var(--bad);font-size:11.5px;margin-top:8px;min-height:14px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.app{display:flex;min-height:100vh}
.side{width:210px;flex-shrink:0;background:rgba(15,22,35,.55);border-right:1px solid var(--bd);padding:20px 14px;display:flex;flex-direction:column;position:fixed;top:0;left:0;height:100vh;overflow-y:auto}
.sbrand{display:flex;align-items:center;gap:11px;margin-bottom:24px}
.sbrand .bt{font-weight:600;font-size:16.5px;line-height:1.3;color:var(--hi);letter-spacing:.2em;font-feature-settings:"palt"}
.sbrand .bt small{display:block;font-size:9px;letter-spacing:.34em;color:var(--mut);font-weight:500;margin-top:6px;font-family:var(--mono)}
.pg{background:linear-gradient(92deg,var(--g1),var(--g2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;font-style:normal}
.orb{position:relative;border-radius:50%;flex-shrink:0;
background:radial-gradient(circle at 37% 31%,#f2fffc 0%,#8ff3e6 17%,#3fe0c5 42%,#1aa6cf 71%,#083f57 100%);
box-shadow:0 0 0 1px rgba(143,243,230,.22),0 0 12px 1px rgba(63,224,197,.55),0 0 26px 5px rgba(63,224,197,.22),inset -3px -4px 9px rgba(2,26,38,.7),inset 2px 2px 7px rgba(255,255,255,.3);
animation:orbglow 3.4s ease-in-out infinite}
.orb::after{content:"";position:absolute;top:13%;left:19%;width:36%;height:28%;border-radius:50%;
background:radial-gradient(circle,rgba(255,255,255,.95),rgba(255,255,255,0) 70%)}
@keyframes orbglow{0%,100%{box-shadow:0 0 0 1px rgba(143,243,230,.22),0 0 12px 1px rgba(63,224,197,.5),0 0 24px 4px rgba(63,224,197,.18),inset -3px -4px 9px rgba(2,26,38,.7),inset 2px 2px 7px rgba(255,255,255,.3)}50%{box-shadow:0 0 0 1px rgba(143,243,230,.3),0 0 16px 2px rgba(63,224,197,.7),0 0 34px 7px rgba(63,224,197,.3),inset -3px -4px 9px rgba(2,26,38,.7),inset 2px 2px 7px rgba(255,255,255,.34)}}
.nav{display:flex;flex-direction:column;gap:2px}
.ni{padding:9px 13px;border-radius:9px;cursor:pointer;color:var(--mut);font-weight:600;font-size:13px;letter-spacing:.3px;transition:.14s}
.ni:hover{color:var(--hi);background:rgba(255,255,255,.04)}
.ni.on{background:linear-gradient(90deg,var(--acc2),transparent 86%);color:var(--acc);box-shadow:inset 2px 0 0 var(--g2);font-weight:700}
.ni.sub{padding-left:24px;font-size:12.5px}
.nigrp{margin:13px 0 5px;padding:13px 13px 0;font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:var(--mut);font-weight:700;border-top:1px solid var(--bd)}
.sfoot{margin-top:auto;color:var(--mut);font-size:11px;font-family:var(--mono);padding:12px 13px 0;border-top:1px solid var(--bd)}
main{flex:1;min-width:0;margin-left:210px;padding:26px 32px;display:flex;justify-content:center}
.inner{width:100%;max-width:1040px}
.b-mini{padding:7px 12px;font-size:11.5px}
.suser{color:var(--tx);font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:7px;min-width:0}
.suser .dot{width:7px;height:7px;border-radius:50%;background:var(--g2);box-shadow:0 0 8px var(--g2);flex-shrink:0}
.lgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));gap:14px}
.doc{max-width:880px}
.doc h2{font-size:18px;margin:2px 0 6px;color:var(--hi)}
.doc .sub2{color:var(--mut);font-size:12.5px;margin-bottom:16px;line-height:1.6}
.doc .lcard{margin-bottom:14px}
.doc h3{margin:0 0 10px;font-size:14.5px;color:var(--acc);display:flex;align-items:center}
.doc p{margin:7px 0;line-height:1.75;color:var(--tx)}
.doc ul,.doc ol{margin:7px 0;padding-left:19px;line-height:1.78}
.doc li{margin:6px 0;color:var(--tx)}
.doc b{color:var(--hi);font-weight:600}
.doc .step{display:inline-flex;width:23px;height:23px;align-items:center;justify-content:center;border-radius:6px;background:var(--acc2);color:var(--acc);font-weight:700;font-family:var(--mono);margin-right:9px;font-size:12.5px}
.doc .jump{color:var(--acc);cursor:pointer;border-bottom:1px dashed var(--acc)}
.doc .jump:hover{filter:brightness(1.15)}
.doc .tip{background:var(--warnbg);border:1px solid rgba(255,178,89,.3);color:var(--warn);border-radius:8px;padding:9px 12px;font-size:12.5px;margin:10px 0 2px;line-height:1.65}
.lcard{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--bd);border-radius:13px;padding:16px 18px;box-shadow:0 14px 30px -24px rgba(0,0,0,.8)}
.lcard h3{margin:0 0 12px;font-size:12px;letter-spacing:.8px;text-transform:uppercase;color:var(--mut);display:flex;align-items:center;gap:8px;font-weight:700}
.linkitem{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid var(--bd);border-radius:9px;margin-bottom:8px;color:var(--tx);text-decoration:none;transition:.14s}
.linkitem:last-child{margin-bottom:0}
.linkitem:hover{border-color:var(--g2);background:rgba(63,224,197,.06);color:var(--hi)}
.linkitem .nm{font-weight:600;font-size:13px}
.linkitem .d{color:var(--mut);font-size:10.5px;font-family:var(--mono);white-space:nowrap}
.linkitem:hover .d{color:var(--acc)}
.logbox{background:#060a11;border:1px solid var(--bd);border-radius:9px;padding:12px 14px;font-family:var(--mono);font-size:11.5px;line-height:1.55;color:#9fb8cc;max-height:400px;overflow:auto;white-space:pre-wrap;word-break:break-all;margin-top:9px}
.logbox::-webkit-scrollbar{width:9px;height:9px}.logbox::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:6px}
select{background:#0c1320;border:1px solid var(--bd2);color:var(--tx);border-radius:9px;padding:7px 9px;font-family:inherit;font-size:12px;cursor:pointer}
</style></head><body>
<script>try{if(localStorage.getItem('pearl_theme')!='dark')document.documentElement.setAttribute('data-theme','light');}catch(e){document.documentElement.setAttribute('data-theme','light');}</script>
<div id=login style=display:none><div class=box>
<div style="display:flex;align-items:center;gap:18px;margin-bottom:14px">
<span class=orb style="width:42px;height:42px"></span>
<div><div class=logo>// PEARL_SNIPER v1</div><h2 style="margin:2px 0 0;font-weight:600;letter-spacing:.2em">今晚挖<i class=pg>珍珠</i></h2></div></div>
<div class=sub>PEARL SNIPER DASHBOARD</div>
<input id=pw type=password placeholder="ACCESS PASSWORD" onkeydown="if(event.key=='Enter')login()">
<div class=err id=lerr></div>
<button class=b-acc style="width:100%;margin-top:14px" onclick=login()>登录 / LOGIN</button>
<div class=peek onclick=guestLogin()>👁 偷窥模式 · 仅看仪表盘 / PEEK MODE</div></div></div>

<div class=app>
<aside class=side>
<div class=sbrand><span class=orb style="width:26px;height:26px"></span><span class=bt>今晚挖<i class=pg>珍珠</i><small>PEARL SNIPER v1</small></span></div>
<nav class=nav>
<div class="ni on" data-nav=ov onclick="nav('ov')">仪表盘</div>
<div class="ni" data-nav=lk onclick="nav('lk')">工具集</div>
<div class="nigrp adm">配置工作台</div>
<div class="ni sub adm" data-nav=cf:common onclick="nav('cf:common')">全局配置</div>
<div id=cfaccts></div>
<div class="nigrp">文档</div>
<div class="ni sub" data-nav=doc:guide onclick="nav('doc:guide')">工具说明</div>
<div class="ni sub" data-nav=doc:tutorial onclick="nav('doc:tutorial')">挖珠教程</div>
</nav>
<div class=sfoot><div class=srow><div class=suser><span class=dot></span><span id=uname>admin</span></div><div class=sicons><a class=ghlink href="https://github.com/kuzicode/pearl-wzz-dashboard" target=_blank rel=noopener title="GitHub · pearl-wzz-dashboard"><svg viewBox="0 0 16 16" width=15 height=15 fill=currentColor aria-hidden=true><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a><button class=tbtn id=tbtn onclick=toggleTheme() title="切换亮 / 暗">🌙</button></div></div><div id=clock></div>
<div class=logout onclick=logout()>⏏ 退出登录</div></div>
</aside>
<main><div class=inner><div id=ov></div><div id=lk style=display:none></div><div id=doc style=display:none></div><div id=cf style=display:none></div></div></main>
</div>
<div class=toast id=toast></div>

<script>
let view='ov',subtab='common',docsub='guide',ROLE='admin';
let EDITING=null,BALVAL={};   // 总览余额内联编辑: 正在编辑的账号 / 各账号手填余额预填值
function applyRole(){let adm=ROLE=='admin';
document.querySelectorAll('.adm').forEach(e=>e.style.display=adm?'':'none');
let u=document.getElementById('uname');if(u)u.textContent=adm?'admin':'访客 GUEST';
let dot=document.querySelector('.suser .dot');if(dot)dot.style.background=adm?'var(--g2)':'var(--warn)';
if(!adm&&view=='cf'){nav('ov');}}
function toast(m){let t=document.getElementById('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',2600);}
function nav(t){if(t=='ov'){view='ov';}else if(t=='lk'){view='lk';}else if(t.indexOf('doc:')==0){view='doc';docsub=t.split(':')[1];}else{view='cf';subtab=t.split(':')[1];}
document.querySelectorAll('.ni').forEach(e=>e.classList.toggle('on',e.dataset.nav==t));
document.getElementById('ov').style.display=view=='ov'?'':'none';
document.getElementById('lk').style.display=view=='lk'?'':'none';
document.getElementById('doc').style.display=view=='doc'?'':'none';
document.getElementById('cf').style.display=view=='cf'?'':'none';refresh();}
function copyAddr(a){(navigator.clipboard?navigator.clipboard.writeText(a):Promise.reject()).then(()=>toast('钱包地址已复制')).catch(()=>toast('复制失败, 请手动选中'));}
async function api(p,opt){const r=await fetch(p,opt);if(r.status==401){document.getElementById('login').style.display='flex';throw 'auth';}document.getElementById('login').style.display='none';return r.json();}
function afterAuth(role){ROLE=role||'admin';document.getElementById('login').style.display='none';if(ROLE!='admin'&&view=='cf')view='ov';applyRole();refresh();}
async function login(){const pw=document.getElementById('pw').value;
const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
if(r.ok){const d=await r.json();afterAuth(d.role);}else{const d=await r.json();document.getElementById('lerr').textContent=d.error||'登录失败';}}
async function guestLogin(){const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guest:true})});
if(r.ok){const d=await r.json();afterAuth(d.role||'guest');}}
async function logout(){try{await fetch('/logout',{method:'POST'});}catch(e){}location.reload();}
async function initRole(){try{const m=await fetch('/api/me');if(m.ok){const d=await m.json();ROLE=d.role;applyRole();}}catch(e){}}
function applyTheme(t){let light=t=='light';document.documentElement.setAttribute('data-theme',light?'light':'dark');let b=document.getElementById('tbtn');if(b)b.textContent=light?'☀️':'🌙';let mc=document.querySelector('meta[name=theme-color]');if(mc)mc.setAttribute('content',light?'#f3f6fc':'#0a0e17');}
function initTheme(){let t='light';try{t=localStorage.getItem('pearl_theme')||'light';}catch(e){}applyTheme(t);}
function toggleTheme(){let cur=document.documentElement.getAttribute('data-theme')||'dark';let nx=cur=='light'?'dark':'light';try{localStorage.setItem('pearl_theme',nx);}catch(e){}applyTheme(nx);}
function esc(s){return (s==null?'':''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function dur(s){if(s==null)return '-';let h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h+'h'+m+'m';}
function fnum(n,d){if(n==null)return '-';n=Number(n);if(Math.abs(n)<1e-9)n=0;return n.toLocaleString(undefined,{maximumFractionDigits:d==null?2:d});}
async function resetStats(){if(!confirm('确认重置统计? 累计租金 / 产出 / 利润都会清零, 从现在重新起算(币价保留)。'))return;try{let r=await api('/api/reset-stats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});if(r&&r.ok){toast('统计已重置, 从现在起算');refresh();}else toast((r&&r.error)||'重置失败');}catch(e){}}

async function renderOverview(){if(EDITING)return;let d,r;try{let pv=localStorage.getItem('pool_view')||'merged';d=await api('/api/summary?pool='+encodeURIComponent(pv));r=await api('/api/rentals')}catch(e){return}
if(ROLE=='admin'){let _ce=document.getElementById('cfaccts');if(_ce)_ce.innerHTML=Object.keys(r).map(a=>`<div class="ni sub adm${(view=='cf'&&subtab==a)?' on':''}" data-nav=cf:${a} onclick="nav('cf:${a}')">${esc((r[a]&&r[a].label)||a)}</div>`).join('');}
let acct='https://pearlhash.xyz/account/'+encodeURIComponent(d.wallet);
let pe=d.pool_error?`<div class=muted style="color:var(--warn);margin-top:10px">POOL_API: ${esc(d.pool_error)}</div>`:'';
let bp=Object.entries(d.running_by_platform).map(([k,v])=>`${k} ${v}`).join('  ·  ');
let ssd=d.stats_since?new Date(d.stats_since*1000):null;let ssl=ssd?((ssd.getMonth()+1)+'-'+ssd.getDate()+' '+String(ssd.getHours()).padStart(2,'0')+':'+String(ssd.getMinutes()).padStart(2,'0')):'';
let pbasis=d.produced_basis||'mixed';
let plabel=pbasis=='since_reset'?('自重置起算'+(ssl?(' (统计自 '+ssl+')'):'')):(pbasis=='all_time'?'全期(已付+未付)':'PearlHash 自重置 + TW Pool 全期');
let proflabel=pbasis=='since_reset'?'产出折合 − 累计租金':'产出折合 − 累计租金 · ⚠ 口径不一(全期产出 vs 自重置租金), 仅供参考';
let wk=(d.workers||[]).map(w=>`<tr><td>${esc(w.name)}</td><td>${esc((w.gpus||[]).join(', '))}</td><td><b style=color:var(--acc)>${fnum(w.th)}</b> TH/s</td><td>${esc(w.ip)}</td></tr>`).join('')||'<tr><td colspan=4 class=muted>矿池暂无在挖 worker</td></tr>';
let plat='';for(const aid of Object.keys(r).sort((a,b)=>((r[b]&&r[b].machines||[]).length)-((r[a]&&r[a].machines||[]).length))){const v=r[aid];const p=v.platform||aid;
let badges=`<span class="pill ${v.process_running?'ok':'bad'}">${v.process_running?'RUNNING':'STOPPED'}</span>`+(v.rent_paused?'<span class="pill warn">RENT PAUSED</span>':'');
let balTxt;if(v.balance!=null){let t=(v.hours_left!=null)?('约 '+fnum(v.hours_left,1)+'h 花完'):(v.burn_hourly>0?'':'当前无消耗');let lab=v.balance_estimated?'估算余额':'余额';balTxt=`${lab} $${fnum(v.balance,2)}${t?' · '+t:''}`;}else{balTxt='余额 —';}
let bh;if(v.balance_editable){BALVAL[aid]=(v.balance_usd!=null?v.balance_usd:'');bh=`<span class="bal editable" id="bal_${esc(aid)}" onclick="editBal('${esc(aid)}')" title="点击填写/修改余额(此平台无余额 API, 手动维护)">${balTxt} <span class=ed-pen>✎</span></span>`;}else{bh=`<span class=bal>${balTxt}</span>`;}
let sstat='';if(p=='salad'){let s=v.salad_status||{};let pr=[];if(s.running_count!=null)pr.push('运行 '+s.running_count);if(s.allocating_count)pr.push('分配中 '+s.allocating_count);let gc=(v.salad_gpu_classes||[]).join(' / ');let serr=(v.salad_error&&!(v.machines||[]).length)?' · '+esc(v.salad_error):'';sstat=`<div class=muted style=margin-bottom:9px>SALAD 实时 · ${pr.join(' · ')||'-'}${gc?' · GPU 档 '+esc(gc):''}${serr}</div>`;}
let rows=(v.machines||[]).map(m=>{let a=(ROLE=='admin'&&m.id)?`<button class=b-bad onclick="term('${aid}','${p}','${esc(m.id)}','${esc(m.group||'')}')">关闭</button>`:'';

let price=m.price_label?esc(m.price_label):(m.price==null?'-':'$'+fnum(m.price,3)+'/h');
let gpu=(m.gpu&&m.gpu!='?')?esc(m.gpu):'<span class=muted>—</span>';
return `<tr>${p=='salad'?('<td>'+esc(m.group||'')+'</td>'):''}<td>${esc(m.id)}</td><td>${gpu}</td><td>${price}</td><td>${dur(m.duration_seconds)}</td><td>${m.hashrate_th==null?'<span class=muted>—</span>':fnum(m.hashrate_th)+' TH/s'}</td><td>${a}</td></tr>`;}).join('')||`<tr><td colspan=${p=='salad'?7:6} class=muted>无在跑机器</td></tr>`;
plat+=`<div class=platbox><div class=top><b>${esc(v.label||aid)}</b>${badges}${bh}</div>${sstat}
<table><tr>${p=='salad'?'<th>组</th>':''}<th>实例</th><th>GPU</th><th>单价</th><th>时长</th><th>算力</th><th></th></tr>${rows}</table></div>`;}
document.getElementById('ov').innerHTML=`
<div class="card wallet">
<div style=min-width:0><div class=k>WALLET · 钱包地址</div><div class=addr>${esc(d.wallet)}</div></div>
<div class=row style=flex-shrink:0;gap:8px>
<button class=b-mini onclick="copyAddr('${esc(d.wallet)}')">复制</button>
<div class=go onclick="window.open('${acct}','_blank')">PearlHash →</div>
<select id=poolView onchange="setPoolView(this.value)" title="切换显示的矿池(仅显示, 不影响挖矿)"><option value=merged>合并</option><option value=pearlhash>PearlHash</option><option value=twpool>TW Pool</option></select></div></div>
<div class=cards>
<div class=card><div class=k>在跑机器</div><div class=v>${d.running_machines}</div><div class=sub>${esc(bp)}</div></div>
<div class=card><div class=k>总算力 矿池实测</div><div class=v>${fnum(d.total_hashrate_th)} <small>TH/s</small></div></div>
<div class=card><div class=k>累计租金</div><div class=v>$${fnum(d.cumulative_rent_usd)}</div><div class=sub>$${fnum(d.current_hourly_usd)}/h · 自重置起算</div></div>
<div class=card><div class=k>累计产出</div><div class=v style=color:var(--acc)>${fnum(d.cumulative_output,4)} <small>PEARL</small></div><div class=sub>≈ $${fnum(d.cumulative_output_usd)} · ${plabel} @ $${fnum(d.coin_price_usd,2)}/币${d.coin_price_live?' <span style="color:var(--ok);font-size:10px">实时</span>':''}</div></div>
<div class=card><div class=k>矿池余额</div><div class=v>${d.pool_balance==null?'<span class=muted>—</span>':fnum(d.pool_balance,4)+' <small>PEARL</small>'}</div><div class=sub>${pbasis=='all_time'?'TW Pool':(pbasis=='since_reset'?'PearlHash':'两池合计')}</div></div>
<div class=card><div class=k>累计折合利润</div><div class=v style="color:${d.cumulative_profit_usd>=0?'var(--acc)':'#ff6b6b'}">$${fnum(d.cumulative_profit_usd)}</div><div class=sub>${proflabel}</div></div>
</div>
${ROLE=='admin'?`<div class=row style="gap:10px;margin-top:12px;align-items:center;flex-wrap:wrap">
<span class=muted style="font-size:12px">PRL/USDT <b style="color:var(--hi);font-family:var(--mono)">$${fnum(d.coin_price_usd,4)}</b>${d.coin_price_live?' <span style="color:var(--ok);font-size:10px;letter-spacing:.4px">● 实时</span>':' <span style="color:var(--warn);font-size:10px">离线</span>'}</span>
<button class=b-bad onclick="resetStats()">重置统计</button>
${ssl?`<span class=muted style="font-size:12px">统计自 ${ssl} 起算</span>`:''}
</div>`:''}${pe}
<div class="kpanel" id=kpanel>
<div class=khead onclick="toggleKline()">
  <span class=ktit>📈 PRL/USDT 行情</span>
  <span class=kprice id=kp_price>$${fnum(d.coin_price_usd,4)}</span>
  <span class="kdelta" id=kp_delta></span>
  <div class=kpers>
    ${['15m','1h','4h','1d'].map(p=>`<span class="kper${p=='15m'?' on':''}" onclick="event.stopPropagation();setKPer('${p}')">${p}</span>`).join('')}
  </div>
  <span class=karr>▼</span>
</div>
<div class=kbody id=kbody>
  <div class=kema-legend><span><i style="background:#f7c948"></i>EMA20</span><span><i style="background:#a78bfa"></i>EMA60</span></div>
  <div class=kcanvas-wrap id=kwrap><canvas class=kc id=kcanvas height=340></canvas><div class=ktip id=ktip></div></div>
  <div class=kcanvas-wrap id=kwrap2 style=margin-top:4px><canvas class=kc id=kvcanvas height=70></canvas></div>
</div>
</div>
<div class=sec><div class=lbl>矿池在挖 WORKER</div><table><tr><th>Worker</th><th>GPU</th><th>算力</th><th>IP</th></tr>${wk}</table></div>
<div class=sec><div class=lbl>各平台租用情况</div>${plat}</div>`;
let _pvsel=document.getElementById('poolView'); if(_pvsel)_pvsel.value=localStorage.getItem('pool_view')||'merged';
// renderOverview 每次重建 DOM 后恢复 K线展开状态
if(_kopen){const kp=document.getElementById('kpanel');if(kp){kp.classList.add('open');if(_kdata)setTimeout(()=>drawKline(_kdata),0);else loadKline();}}
}

// ---------- K线图 ----------
let _kper='15m',_kopen=false,_kdata=null;
const _kperMap={'15m':15,'1h':60,'4h':240,'1d':1440};
function toggleKline(){_kopen=!_kopen;const p=document.getElementById('kpanel');if(p)p.classList.toggle('open',_kopen);if(_kopen&&!_kdata)loadKline();}
function setKPer(p){_kper=p;document.querySelectorAll('.kper').forEach(e=>e.classList.toggle('on',e.textContent==p));_kdata=null;if(_kopen)loadKline();}
async function loadKline(){
  const period=_kperMap[_kper]||15;
  try{_kdata=await api('/api/kline?period='+period);}catch(e){return;}
  if(_kdata&&_kdata.length)drawKline(_kdata);
}
function ema(closes,n){
  const k=2/(n+1),r=[];let e=null;
  for(let i=0;i<closes.length;i++){if(e===null){e=closes[i];}else{e=closes[i]*k+e*(1-k);}r.push(e);}
  return r;
}
function drawKline(data){
  const cc=document.getElementById('kcanvas');const vc=document.getElementById('kvcanvas');
  if(!cc||!vc)return;
  const DPR=window.devicePixelRatio||1;
  const W=cc.parentElement.clientWidth;const CH=340,VH=70;
  cc.width=W*DPR;cc.height=CH*DPR;cc.style.width=W+'px';cc.style.height=CH+'px';
  vc.width=W*DPR;vc.height=VH*DPR;vc.style.width=W+'px';vc.style.height=VH+'px';
  const ctx=cc.getContext('2d');ctx.scale(DPR,DPR);
  const vctx=vc.getContext('2d');vctx.scale(DPR,DPR);
  const N=data.length;if(!N)return;
  const PAD={l:52,r:12,t:10,b:28};
  const cW=W-PAD.l-PAD.r,cH=CH-PAD.t-PAD.b;
  const opens=data.map(d=>parseFloat(d[1])),highs=data.map(d=>parseFloat(d[2]));
  const lows=data.map(d=>parseFloat(d[3])),closes=data.map(d=>parseFloat(d[4]));
  const vols=data.map(d=>parseFloat(d[5]));
  const pMin=Math.min(...lows),pMax=Math.max(...highs),pRange=pMax-pMin||0.001;
  const vMax=Math.max(...vols)||1;
  const candleW=Math.max(1,Math.min(12,Math.floor(cW/N*0.72)));
  const step=cW/N;
  const px=(price)=>PAD.t+cH-(price-pMin)/pRange*cH;
  const xc=(i)=>PAD.l+i*step+step/2;
  // grid
  ctx.strokeStyle='rgba(255,255,255,.05)';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){const y=PAD.t+cH/4*i;ctx.beginPath();ctx.moveTo(PAD.l,y);ctx.lineTo(W-PAD.r,y);ctx.stroke();}
  // price labels
  ctx.fillStyle='rgba(121,131,156,.7)';ctx.font='10px Inter,sans-serif';ctx.textAlign='right';
  for(let i=0;i<=4;i++){const p=pMax-(pRange/4)*i;ctx.fillText('$'+p.toFixed(4),PAD.l-4,PAD.t+cH/4*i+4);}
  // candles
  data.forEach((d,i)=>{
    const o=opens[i],h=highs[i],l=lows[i],c=closes[i];
    const up=c>=o;const col=up?'#3fe0c5':'#ff7a7a';
    ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;
    const x=xc(i);
    ctx.beginPath();ctx.moveTo(x,px(h));ctx.lineTo(x,px(l));ctx.stroke();
    const by=Math.min(px(o),px(c));const bh=Math.max(1,Math.abs(px(o)-px(c)));
    ctx.fillRect(x-candleW/2,by,candleW,bh);
  });
  // EMA 20
  const e20=ema(closes,20),e60=ema(closes,60);
  [[e20,'#f7c948',1.5],[e60,'#a78bfa',1.5]].forEach(([vals,col,lw])=>{
    ctx.strokeStyle=col;ctx.lineWidth=lw;ctx.beginPath();let started=false;
    vals.forEach((v,i)=>{if(v===null)return;const x=xc(i),y=px(v);started?(ctx.lineTo(x,y)):(ctx.moveTo(x,y),started=true);});
    ctx.stroke();
  });
  // x-axis time labels
  const fmt=(ts)=>{const d=new Date(ts*1000);
    if(_kper=='1d')return(d.getMonth()+1)+'-'+d.getDate();
    return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};
  ctx.fillStyle='rgba(121,131,156,.7)';ctx.textAlign='center';ctx.font='9.5px Inter,sans-serif';
  const step2=Math.max(1,Math.floor(N/8));
  for(let i=0;i<N;i+=step2){ctx.fillText(fmt(data[i][0]),xc(i),CH-6);}
  // volume bars
  vctx.clearRect(0,0,W,VH);
  data.forEach((d,i)=>{
    const v=vols[i],up=closes[i]>=opens[i];
    vctx.fillStyle=up?'rgba(63,224,197,.55)':'rgba(255,122,122,.55)';
    const bh=Math.max(1,(v/vMax)*(VH-6));
    vctx.fillRect(xc(i)-candleW/2,VH-bh,candleW,bh);
  });
  // delta label
  if(N>1){const first=closes[0],last=closes[N-1];const pct=((last-first)/first*100).toFixed(2);
    const el=document.getElementById('kp_delta');
    if(el){el.textContent=(pct>=0?'+':'')+pct+'%';el.style.color=pct>=0?'var(--ok)':'var(--bad)';}}
  // crosshair
  attachCrosshair(cc,vc,data,px,xc,step,PAD,CH,W,candleW);
}
function attachCrosshair(cc,vc,data,px,xc,step,PAD,CH,W,candleW){
  cc.onmousemove=function(e){
    const rect=cc.getBoundingClientRect();const mx=(e.clientX-rect.left)*(cc.width/cc.clientWidth/window.devicePixelRatio||1);
    const i=Math.round((mx-PAD.l)/step-0.5);if(i<0||i>=data.length)return;
    const d=data[i];const tip=document.getElementById('ktip');if(!tip)return;
    const dt=new Date(d[0]*1000);
    tip.style.display='block';
    tip.innerHTML=`<b>${dt.toLocaleDateString()} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}</b><br>`+
      `O <b>${(+d[1]).toFixed(4)}</b>  H <b style=color:var(--ok)>${(+d[2]).toFixed(4)}</b>  L <b style=color:var(--bad)>${(+d[3]).toFixed(4)}</b>  C <b>${(+d[4]).toFixed(4)}</b><br>`+
      `Vol <b>${(+d[5]).toLocaleString()}</b>`;
    // reposition away from right edge
    const tipW=180;const lx=e.clientX-rect.left;
    tip.style.left=(lx+tipW>rect.width?lx-tipW-8:lx+12)+'px';
  };
  cc.onmouseleave=()=>{const t=document.getElementById('ktip');if(t)t.style.display='none';};
}

let CFG=null;
async function renderConfigTab(){let d;try{d=await api('/api/full-config')}catch(e){return}CFG=d;
let nv=Object.keys(d.platforms).map(a=>`<div class="ni sub adm${subtab==a?' on':''}" data-nav=cf:${a} onclick="nav('cf:${a}')">${esc(d.platforms[a].label||a)}</div>`).join('');
let ce=document.getElementById('cfaccts');if(ce)ce.innerHTML=nv;
document.getElementById('cf').innerHTML=subtab=='common'?commonHtml(d):platformHtml(d.platforms[subtab],subtab);}
function commonHtml(d){let c=d.common;let diff=d.common_diff||{};
let cf=(k,label,req,ph)=>{let w='';
if(diff[k]){let dv=Object.entries(diff[k]).map(([p,v])=>p+'='+(v==null||v===''?'∅':v)).join('   |   ');
w=` <span class=cdiff title="${esc(dv)}">⚠ 各平台当前不一致, 保存将统一覆盖</span>`;}
return `<div class=fld>${label}${req?' <span class=req>必填</span>':''}${w}</div><input id="cm_${k}" value="${esc(c[k]==null?'':c[k])}" placeholder="${ph||''}">`;};
return `<div class=lbl>全局配置 · COMMON</div>
<div class=row style="margin-bottom:10px"><button class=b-warn onclick="migrateAll()">⇄ 一键全部账号迁移到…</button></div>
<div class=platbox><div class=top><b>COMMON</b><span class=muted>当前值取自 vast · 保存会写入全部 4 个 config(覆盖各平台同名字段)</span></div>
<div class=grid2>
${cf('prl_address','钱包地址 prl_address',1,'你的 $pearl 钱包, 否则挖给别人')}
${cf('prl_host','矿池 prl_host',0,'84.32.220.219:9000')}
${cf('worker_prefix','worker 前缀',0,'auto')}
${cf('image','矿机镜像 image',0,'docker.io/kuzigmgm/pearl-miner:v11')}
${cf('max_active_instances','最多同时租 (台)',0,'1')}
${cf('max_total_hourly_usd','总时租上限 ($/h)',0,'1.0')}
${cf('alert_url','告警 URL (可空)',0,'ntfy 等')}
</div><div class=row style=margin-top:12px><button class=b-acc onclick=saveCommon()>保存全局配置</button>
<span class=hint>保存后各平台需「重启应用」生效</span></div></div>
<div class=platbox><div class=top><b>账户 · 看板登录</b></div>
<div class=grid2>
<div class=fld>用户名</div><input value="admin" disabled>
<div class=fld>新密码</div><input id=newpw type=password placeholder="至少 4 位">
</div><div class=row style=margin-top:12px><button class=b-acc onclick=savePw()>更新密码</button>
<span class=hint>立即生效, 下次登录用新密码</span></div></div>`;}
function platformHtml(v,p){
let proc=`<span class="pill ${v.process_running?'ok':'mut'}">${v.process_running?'RUNNING':'STOPPED'}</span>`+(v.rent_paused?'<span class="pill warn">RENT PAUSED</span>':'');
let key=v.key_set?`<span class="pill ok">已设置 ${esc(v.key_mask)}</span>`:'<span class="pill bad">未设置</span>';
let gpus=(v.gpus||[]).map((g,i)=>gpuRowHtml(p,i,g)).join('');
let spec=(v.specific||[]).map(s=>specHtml(p,s)).join('');
let rentBtn=v.rent_paused?`<button class=b-acc onclick="toggle('${p}',false)">▶ 启动租用</button>`:`<button class=b-warn onclick="toggle('${p}',true)">⏸ 暂停租用</button>`;
return `<div class=lbl>${esc(v.label||p)} · 平台配置</div>
<div class=platbox id=box_${p}><div class=top><b>${esc(v.label||p)}</b>${proc}</div>
<div class=grid2>
<div class=fld>启用 enabled</div><div><input type=checkbox id="en_${p}" ${v.enabled?'checked':''}></div>
${v.has_create?`<div class=fld>自动建机 create_enabled</div><div><input type=checkbox id="ce_${p}" ${v.create_enabled?'checked':''}></div>`:''}
<div class=fld>新抢矿池 pool</div><div><select id="pool_${p}" onchange="setPool('${esc(p)}',this.value)">${(CFG.pools||[]).map(o=>`<option value="${o.id}" ${v.pool==o.id?'selected':''}>${esc(o.label)}</option>`).join('')}</select> <button class=b-warn onclick="migrateAcct('${esc(p)}')">⇄ 迁移现有机器到所选池</button></div>
</div>
<div class=lbl style=margin-top:14px>GPU 型号 · 最高 $/h · 最低 TH/s</div>
<div class=gpurow style=color:var(--mut);font-size:11px><div>GPU</div><div>最高 $/h</div><div>最低 TH/s</div><div></div></div>
<div id="gpus_${p}">${gpus}</div>
<button onclick="addGpu('${p}')" style=margin-top:4px>+ 增加 GPU</button>
${spec?`<div class=lbl style=margin-top:16px>平台特定参数</div><div class=grid2>${spec}</div>`:''}
<hr class=divider>
<div class=fld style=margin-bottom:6px>API KEY · <b>${esc(v.key_name)}</b> ${key} <span class=req>必填</span></div>
<div class=row><input id="k_${p}" type=password placeholder="填入/更新 ${esc(v.key_name)}"><button onclick="savekey('${p}')">存 KEY</button></div>
<div class=row style=margin-top:14px>
<button class=b-acc onclick="savePlat('${p}')">保存配置</button>
<button onclick="restart('${p}')">重启应用</button>
${rentBtn}
<span class=hint>保存后点「重启应用」才生效</span></div>
<details><summary>高级 · raw JSON (config.${p}.json 全文)</summary>
<textarea id="raw_${p}">${esc(v.raw)}</textarea>
<div class=row style=margin-top:8px><button class=b-acc onclick="saveRaw('${p}')">保存 raw JSON</button><span class=hint>整体覆盖该文件, 写前自动 .bak</span></div></details>
<hr class=divider>
<div class=row><button onclick="loadLog('${p}')">📜 查看后台日志</button>
<select id="loglines_${p}" onchange="loadLog('${p}')"><option value=100>最近 100 行</option><option value=300 selected>最近 300 行</option><option value=500>最近 500 行</option></select>
<span class=hint>logs/${p}.log · 实时后台输出</span></div>
<pre class=logbox id="log_${p}" style=display:none></pre>
</div>`;}
async function savePw(){let pw=document.getElementById('newpw').value;if(pw.length<4){toast('密码至少 4 位');return;}
let r=await api('/api/dashboard-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
document.getElementById('newpw').value='';toast(r.error?('失败: '+r.error):'看板密码已更新');}
async function setPool(aid,pool){let r;try{r=await api('/api/set-pool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:aid,pool:pool})});}catch(e){toast('切换失败');return;}toast(r&&r.ok?('已切换新抢矿池: '+pool+'(重启应用后新机器生效)'):('失败: '+((r&&r.error)||'未知')));renderConfigTab();}
function setPoolView(v){localStorage.setItem('pool_view',v);renderOverview();}
async function loadLog(p){let n=document.getElementById('loglines_'+p).value;let pre=document.getElementById('log_'+p);
pre.style.display='';pre.textContent='加载中…';
try{let r=await api('/api/logs?platform='+p+'&lines='+n);pre.textContent=r.log||'(空)';pre.scrollTop=pre.scrollHeight;}catch(e){pre.textContent='加载失败';}}
async function _migrateConfirm(label,target,cntText){
  let word=prompt('【一键迁移】将把 '+label+' 迁移到矿池 ['+target+']。\n'+cntText+'\n迁移期间这些机器会停机重启几分钟(runpod 原地换镜像/vast 销毁重租/salad 重建)。\n\n输入 MIGRATE 确认:');
  return word==='MIGRATE';
}
async function _countAffected(aid){
  try{
    let rt=await api('/api/rentals');
    let total=0,lines=[];
    Object.entries(rt||{}).forEach(([acct,v])=>{
      if(aid!=='all'&&acct!==aid)return;
      let n=(v.machines||[]).length||0;total+=n;
      if(n)lines.push(acct+': '+n+' 台');
    });
    return total?('受影响约 '+total+' 台 ('+lines.join(', ')+')'):'当前无在租机器(只切换配置)';
  }catch(e){return '(获取在租信息失败, 请谨慎确认)';}
}
async function migrateAcct(aid){
  let sel=document.getElementById('pool_'+aid);if(!sel){toast('找不到矿池选择');return;}
  let target=sel.value;
  let cnt=await _countAffected(aid);
  if(!await _migrateConfirm(aid,target,cnt)){toast('已取消(确认词不符)');return;}
  toast('迁移中…(请稍候)');
  let r;try{r=await api('/api/migrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:aid,target_pool:target,confirm:'MIGRATE'})});}catch(e){toast('迁移请求失败');return;}
  _migrateToast(r);renderConfigTab();
}
async function migrateAll(){
  let pools=(CFG&&CFG.pools)||[];
  let target=prompt('全部账号迁移到哪个矿池? 可选: '+pools.map(o=>o.id).join(' / '));
  if(!target){toast('已取消');return;}
  if(!pools.some(o=>o.id===target)){toast('未知矿池: '+target);return;}
  let cnt=await _countAffected('all');
  if(!await _migrateConfirm('全部账号',target,cnt)){toast('已取消(确认词不符)');return;}
  toast('全部迁移中…(请稍候)');
  let r;try{r=await api('/api/migrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:'all',target_pool:target,confirm:'MIGRATE'})});}catch(e){toast('迁移请求失败');return;}
  _migrateToast(r);renderConfigTab();
}
function _migrateToast(r){
  if(!r||!r.ok){toast('迁移失败: '+((r&&r.error)||'未知'));return;}
  let parts=(r.accounts||[]).map(a=>{let res=a.result||{};if(res.error)return a.account+': 错误';let sm=res.summary||{};let ok=(sm.runpod||0)+(sm.vast||0)+(sm.salad||0);let f=sm.failed||0;return a.account+': '+ok+'台'+(f?(' / '+f+'失败'):'');});
  toast('迁移完成 → '+r.target_pool+': '+parts.join(' | ')+'。重启对应监控后新抢才用新池。');
}

function gpuRowHtml(p,i,g){return `<div class=gpurow data-gpu>
<input value="${esc(g.gpu||'')}" placeholder="RTX 4090" data-f=gpu>
<input value="${esc(g.max_price==null?'':g.max_price)}" placeholder="0.4" data-f=price>
<input value="${esc(g.min_hashrate==null?'':g.min_hashrate)}" placeholder="220" data-f=hash>
<button class=b-bad onclick="this.parentNode.remove()">×</button></div>`;}
function addGpu(p){document.getElementById('gpus_'+p).insertAdjacentHTML('beforeend',gpuRowHtml(p,0,{}));}
function specHtml(p,s){let id=`sp_${p}_${s.key}`;
if(s.type=='bool')return `<div class=fld>${s.key}</div><div><input type=checkbox id="${id}" ${s.value?'checked':''}></div>`;
let val=Array.isArray(s.value)?s.value.join(', '):(s.value==null?'':s.value);
return `<div class=fld>${s.key}${s.type=='list'?' (逗号分隔)':''}</div><input id="${id}" value="${esc(val)}">`;}

function collectGpus(p){let rows=document.querySelectorAll('#gpus_'+p+' [data-gpu]');let th={},mh={};
rows.forEach(r=>{let gpu=r.querySelector('[data-f=gpu]').value.trim();if(!gpu)return;
let pr=parseFloat(r.querySelector('[data-f=price]').value);let h=parseFloat(r.querySelector('[data-f=hash]').value);
let names=[gpu];if(gpu.startsWith('RTX '))names.push('NVIDIA GeForce '+gpu);
names.forEach(n=>{if(!isNaN(pr))th[n]=pr;if(!isNaN(h))mh[n]=h;});});
return {thresholds:th,min_hashrate_th:mh};}

async function savePlat(p){const v=CFG.platforms[p];let patch={enabled:document.getElementById('en_'+p).checked};
if(v.has_create)patch.create_enabled=document.getElementById('ce_'+p).checked;
let g=collectGpus(p);patch.thresholds=g.thresholds;patch.min_hashrate_th=g.min_hashrate_th;
(v.specific||[]).forEach(s=>{let el=document.getElementById('sp_'+p+'_'+s.key);if(!el)return;
if(s.type=='bool')patch[s.key]=el.checked;
else if(s.type=='num'){let n=parseFloat(el.value);if(!isNaN(n))patch[s.key]=n;}
else if(s.type=='list')patch[s.key]=el.value.split(',').map(x=>x.trim()).filter(x=>x);
else patch[s.key]=el.value;});
let r=await api('/api/save-platform',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p,data:patch})});
toast(r.error?('保存失败: '+r.error):p+' 配置已保存, 点重启生效');}
async function saveCommon(){let data={};['prl_address','prl_host','worker_prefix','image','alert_url'].forEach(k=>{let el=document.getElementById('cm_'+k);if(el)data[k]=el.value.trim();});
['max_active_instances','max_total_hourly_usd'].forEach(k=>{let n=parseFloat(document.getElementById('cm_'+k).value);if(!isNaN(n))data[k]=n;});
// P2-E: 若有字段当前各平台不一致, 覆盖前确认
let diff=(CFG&&CFG.common_diff)||{};let clash=Object.keys(data).filter(k=>diff[k]);
if(clash.length){let detail=clash.map(k=>{
let perp=Object.entries(diff[k]).map(([p,v])=>'    '+p+': '+(v==null||v===''?'(空)':v)).join('\n');
let nv=(data[k]===''||data[k]==null)?'(空)':data[k];
return '• '+k+'  → 4 平台统一为: '+nv+'\n'+perp;}).join('\n\n');
if(!confirm('以下字段当前各平台不一致，保存全局配置会把 4 个平台覆盖成同一值:\n\n'+detail+'\n\n确定覆盖？')) return;}
let r=await api('/api/save-common',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:data})});
toast(r.error?('失败: '+r.error):'全局配置已写入全部 config, 各平台点重启生效');}
async function saveRaw(p){let raw=document.getElementById('raw_'+p).value;
let r=await api('/api/save-raw',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p,json:raw})});
toast(r.error?('JSON 拒绝: '+r.error):p+' raw 已保存, 点重启生效');}
async function restart(p){if(!confirm('重启 '+p+' 进程以应用配置?'))return;toast(p+' 重启中…');
let r=await api('/api/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p})});
toast(r.process_running?(p+' 已重启'):(p+' 重启后未运行?')); }
async function savekey(p){const el=document.getElementById('k_'+p);const val=el.value.trim();if(!val)return;
let r=await api('/api/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p,value:val})});
el.value='';toast(r.ok?(p+' API key 已保存'):'失败');renderConfigTab();}
async function toggle(p,paused){await api('/api/rent-toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p,paused:paused})});renderConfigTab();}
async function term(aid,plat,id,group){let label=plat=='salad'?'迁移(reallocate)':'关闭并销毁';
if(!confirm('确定要'+label+'这台机器吗?\n'+aid+' · '+id))return;
let r=await api('/api/terminate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:aid,id:id,group:group})});
toast(r.error?('失败: '+r.error):'已执行 '+id);renderOverview();}
function editBal(aid){EDITING=aid;const el=document.getElementById('bal_'+aid);if(!el)return;el.classList.remove('editable');el.removeAttribute('onclick');el.removeAttribute('title');const cur=(BALVAL[aid]!=null?BALVAL[aid]:'');el.innerHTML=`<span class=bal-edit><span class=cur>$</span><input id="bali_${esc(aid)}" type=number step=0.01 min=0 value="${cur}" placeholder="0.00" onkeydown="balKey(event,'${esc(aid)}')"><button class="bb ok" title=保存 onclick="saveBal('${esc(aid)}')">✓</button><button class="bb x" title=取消 onclick="cancelBal()">✕</button></span>`;const inp=document.getElementById('bali_'+aid);inp.focus();inp.select();}
function balKey(e,aid){if(e.key=='Enter'){e.preventDefault();saveBal(aid);}else if(e.key=='Escape'){e.preventDefault();cancelBal();}}
function cancelBal(){EDITING=null;renderOverview();}
async function saveBal(aid){const inp=document.getElementById('bali_'+aid);if(!inp){EDITING=null;return;}const s=inp.value.trim();let bu=(s===''?null:parseFloat(s));if(s!==''&&!(bu>=0)){toast('请输入有效金额(≥0)');inp.focus();return;}EDITING=null;let r;try{r=await api('/api/save-platform',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:aid,data:{balance_usd:bu}})});}catch(e){toast('保存失败');renderOverview();return;}toast(r&&r.ok?'余额已更新':('失败: '+((r&&r.error)||'未知')));renderOverview();}
const LINKS=[
{t:'官网',i:'🌐',items:[['Pearl Research','https://pearlresearch.ai/']]},
{t:'区块浏览器',i:'🔎',items:[['Explorer','https://explorer.pearlresearch.ai/']]},
{t:'交易平台',i:'💱',items:[['SafeTrade · PRL-USDT','https://safetrade.com/exchange/PRL-USDT'],['Pearl OTC','https://app.pearl-otc.com/'],['OKX Web3 · PRL','https://web3.okx.com/zh-hans/token/ethereum/0x07696dcab55e62cfef953666b29fe1970518cb00']]},
{t:'钱包',i:'👛',items:[['Compute Wallet','https://compute.pearlresearch.ai/wallet']]},
{t:'矿池',i:'⛏️',items:[['PearlHash','http://pearlhash.xyz'],['AlphaPool','https://pearl.alphapool.tech/']]},
{t:'租卡平台',i:'🖥️',items:[['Salad','https://portal.salad.com/'],['RunPod','https://runpod.io?ref=9hx2ahkb'],['TensorDock','https://dashboard.tensordock.com/'],['Vast.ai','https://cloud.vast.ai/']]},
{t:'收益计算器',i:'🧮',items:[['Akakay 计算器','https://pearl.akakay.com/'],['Pearl Dashboard','https://pearl-dashboard-pearl.vercel.app/']]},
];
function dom(u){try{return new URL(u).host}catch(e){return u}}
function renderLinks(){let cards=LINKS.map(c=>`<div class=lcard><h3>${c.i} ${esc(c.t)}</h3>`+
c.items.map(it=>`<a class=linkitem href="${esc(it[1])}" target=_blank rel=noopener><span class=nm>${esc(it[0])}</span><span class=d>${esc(dom(it[1]))} ↗</span></a>`).join('')+`</div>`).join('');
document.getElementById('lk').innerHTML=`<div class=lbl>工具集 · TOOLS</div><div class=lgrid>${cards}</div>`;}
function docGuide(){return `<div class=doc>
<div class=lbl>工具说明 · GUIDE</div>
<h2>这是什么</h2>
<div class=sub2>一句话:一个"自动租 GPU 挖珍珠(PRL)"的调度面板 —— 在 4 个云租卡平台(Salad / Vast / RunPod / TensorDock)上自动找便宜显卡、起矿机挖 PRL,自动淘汰算力差的坏机,把"产出 &gt; 租金"的差价变成你的利润。</div>
<div class=tip>📌 重要:这是一套<b>需要你自己部署运行的开源工具</b>,不是托管网站。你要把这份代码跑在<b>自己的电脑或一台云服务器</b>上,才能看到这个面板。下面先讲怎么把它跑起来。</div>
<div class=lcard><h3>💻 本地部署:怎么把它跑起来</h3>
<p><b>环境:</b>只需 <b>Python 3.10+</b>(纯标准库,无需 pip / docker / 数据库)。跑面板的这台机器<b>不需要显卡</b> —— 它只是"指挥部",真正挖矿的是你在各平台租的远程 GPU。</p>
<p><b>四步起跑:</b></p>
<ol>
<li>拿到本项目代码(git clone 或下载解压)。</li>
<li><b>cp .env.example .env</b> —— 在 <b>.env</b> 里填各平台 API Key,并改掉 <b>DASHBOARD_PASSWORD</b>(默认 123456 务必改)。</li>
<li>把 <b>config.*.json</b> 里的 <b>prl_address</b> 改成你自己的钱包地址。</li>
<li>一条命令起全部:<b>bash scripts/start-all.sh</b>(Windows:<b>start-all.ps1</b>);停全部:<b>stop-all.sh</b>。</li>
</ol>
<p>起好后浏览器打开 <b>http://&lt;这台机器的IP&gt;:8787</b>,用 <b>admin / 你设的密码</b> 登录,就是当前这个面板。</p>
<p><b>跑在哪?两种选择:</b></p>
<ul>
<li><b>自己电脑</b> —— 适合先试玩;地址用 http://localhost:8787。<b>关机/断网就停了。</b></li>
<li><b>云服务器 / VPS</b>(推荐长期跑)—— 24h 不间断、随时随地公网访问。但端口暴露在公网,<b>务必改掉默认密码</b>,否则别人能填 key、启停你的租机。</li>
</ul></div>
<div class=lcard><h3>⚙️ 原理:它到底怎么挖(docker 拉取)</h3>
<p>你<b>不用</b>手动登录每台租来的机器装环境。流程全自动:</p>
<ol>
<li>sniper 调用各平台 API <b>租到一块 GPU</b>。</li>
<li>下单时把一个 <b>docker 矿机镜像</b>(默认 <b>pearl-miner:v11</b>)+ 一组<b>环境变量</b>(你的钱包 PRL_ADDRESS、矿池 PRL_HOST、worker 名等)一起下发给平台。</li>
<li>平台自动 <b>docker pull 拉取镜像</b> → 在租来的 GPU 上跑起容器 → 容器里的矿机<b>连上 PearlHash 矿池开始挖 PRL</b>,收益直接进你的钱包地址。</li>
<li>镜像内矿机会自报算力;面板通过矿池 API <b>盯着每台</b>,算力低于门槛(坏卡 / 老驱动 / 虚标)就让它停、再换一台。</li>
</ol>
<p>所以全程是:<b>租卡 → 自动拉 docker 镜像 → 自动连池挖矿 → 自动盯算力换坏机</b>,你只负责配好参数。</p></div>
<div class=lcard><h3>💰 怎么赚钱(玩法)</h3>
<p>你按小时花钱租云 GPU,GPU 挖出的 PRL 进你的钱包。只要 <b>PRL 产出 × 币价 &gt; GPU 租金</b>,就是净赚。本工具的核心就是把这个差价做正、做大:</p>
<ul>
<li><b>挑便宜卡</b> —— 自动比较各平台 offer,优先单位算力最便宜的。</li>
<li><b>剔坏机</b> —— 持续盯每台实测算力,虚标 / 掉算力 / 驱动不行的自动关掉换机,不让钱白烧。</li>
<li><b>控预算</b> —— 设了总时租上限和最大在跑数,绝不超支。</li>
</ul></div>
<div class=lcard><h3>🧭 面板各区</h3>
<ul>
<li><b>仪表盘</b> —— 钱包地址、在跑机器数、总算力(矿池实测)、累计租金、累计产出(PRL+折合USD)、累计折合利润;可改币价、重置统计、按平台暂停租用 / 关闭单台。</li>
<li><b>工具集</b> —— 官网 / 浏览器 / 钱包 / 矿池 / 租卡平台 / 交易平台 / 计算器 的快捷入口。<span class=jump onclick="nav('lk')">→ 打开</span></li>
<li><b>文档</b> —— 本说明 + 挖珠教程。<span class=jump onclick="nav('doc:tutorial')">→ 挖珠教程</span></li>
<li><b>配置工作台</b>(仅管理员)—— 全局配置 + 各平台配置,见下。</li>
</ul></div>
<div class=lcard><h3>🔧 关键参数(配置工作台)</h3>
<ul>
<li><b>钱包地址 prl_address</b> —— 收益打到这,<b>务必是你自己的钱包</b>,填错就是给别人挖。</li>
<li><b>总时租上限 max_total_hourly_usd</b> —— 所有平台合计每小时最多花多少,防超支。</li>
<li><b>最大在跑数 max_active_instances</b> —— 同时最多开几台。</li>
<li><b>矿机镜像 image / 矿池地址 prl_host</b> —— 一般用默认值即可。</li>
<li><b>各平台:API Key、出价上限、可靠性、GPU 档筛选、健康算力门槛</b> —— 控制只租"够便宜 + 够稳"的卡。</li>
</ul></div>
<div class=tip>🔐 安全:API Key、钱包私钥 / 助记词只存在你部署的那台机器的本地文件(.env / 配置),不进代码仓库;公网部署务必改默认密码,转账、配置前再次确认钱包地址是你自己的。</div>
</div>`;}
function docTutorial(){return `<div class=doc>
<div class=lbl>挖珠教程 · TUTORIAL</div>
<h2>小白四步上手</h2>
<div class=sub2>第一次玩?按 a → b → c → d 走一遍就能跑起来。</div>
<div class=lcard><h3><span class=step>a</span>注册钱包,拿到钱包地址</h3>
<ul>
<li>打开 <span class=jump onclick="nav('lk')">工具集</span> → <b>钱包 · Compute Wallet</b>(compute.pearlresearch.ai/wallet)。</li>
<li>创建或导入钱包,<b>务必备份助记词 / 私钥</b> —— 丢了谁也找不回。</li>
<li>复制你的钱包地址(<b>prl1…</b> 开头),这是收益归属地址,第 c 步要填进配置。</li>
</ul></div>
<div class=lcard><h3><span class=step>b</span>去租卡平台租机器、充值、拿 API Key</h3>
<ul>
<li>打开 <span class=jump onclick="nav('lk')">工具集</span> → <b>租卡平台</b>,选一个或多个(Salad / Vast / RunPod / TensorDock)。</li>
<li>注册账号 → <b>充值余额</b>(没余额起不了机)。</li>
<li>在平台后台找到 <b>API Key / Token</b>,复制备用。</li>
</ul>
<div class=tip>💡 新手建议:先从 1 个平台、小预算试跑,跑通了再加平台、加预算。</div></div>
<div class=lcard><h3><span class=step>c</span>配置参数,启动 miner</h3>
<ul>
<li>回到本面板 → <b>全局配置</b>(需管理员登录):填第 a 步的<b>钱包地址</b>;设<b>总时租上限</b>、<b>最大在跑数</b>控制预算;确认矿机镜像、矿池地址。</li>
<li>到 <b>各平台配置</b> 填对应 <b>API Key</b>、出价上限、GPU 档筛选。</li>
<li>各平台点 <b>重启</b> 生效。之后 sniper 自动租卡、起矿机挖 PRL,<b>仪表盘</b>开始出算力和累计产出。</li>
<li>每个参数啥意思?见 <span class=jump onclick="nav('doc:guide')">工具说明</span>。</li>
</ul></div>
<div class=lcard><h3><span class=step>d</span>卖币获利(以 SafeTrade 为例)</h3>
<ul>
<li>挖到的 PRL 进你的 Compute Wallet,等"累计产出"成熟为已确认余额。</li>
<li>打开 <span class=jump onclick="nav('lk')">工具集</span> → <b>交易平台 · SafeTrade</b>,注册并拿到 SafeTrade 的 <b>PRL 充值地址</b>。</li>
<li>打开 <b>Compute Wallet</b>,发起转账:把 PRL 从你的钱包转到 SafeTrade 的充值地址。</li>
<li>到账后在 SafeTrade <b>卖出 PRL → USDT</b>(挂单或市价)。</li>
<li>算总账:<b>USDT 收入 − 累计租金 = 真实利润</b>(对应仪表盘"累计折合利润")。</li>
</ul>
<div class=tip>⚠️ 转账先用<b>小额测试</b>地址是否正确;留意交易所充提币规则与手续费。</div></div>
</div>`;}
function renderDocs(){document.getElementById('doc').innerHTML = docsub=='tutorial'?docTutorial():docGuide();}
function refresh(){if(view=='ov')renderOverview();else if(view=='lk')renderLinks();else if(view=='doc')renderDocs();else renderConfigTab();}
setInterval(()=>{let c=document.getElementById('clock');if(c)c.textContent=new Date().toLocaleTimeString();},1000);
setInterval(()=>{if(view=='ov')renderOverview();},10000);initTheme();initRole();refresh();
</script></body></html>"""


def main():
    CONTROL_DIR.mkdir(exist_ok=True)
    threading.Thread(target=spend_loop, daemon=True).start()
    threading.Thread(target=_refresh_loop, daemon=True).start()  # 后台预热缓存, 请求只读缓存不阻塞
    port = int(CONF.get("port", 8787))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"pearl dashboard on http://0.0.0.0:{port}  (user={CONF.get('user','admin')})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
