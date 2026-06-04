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
    "tensordock": [("city", "str"), ("storage_gb", "num"), ("vcpu_count", "num"),
                   ("ram_gb", "num"), ("seen_ttl_seconds", "num")],
    "salad": [("organization_name", "str"), ("project_name", "str"), ("include_container_groups", "list"),
              ("low_efficiency_stop_seconds", "num"), ("reallocate_cooldown_seconds", "num"),
              ("alphapool_worker_api_enabled", "bool"), ("alphapool_reallocate_enabled", "bool")],
}
HAS_CREATE = {"runpod", "tensordock"}

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
POOL_TTL = 30.0
_lock = threading.Lock()


# ---------- 读 ----------
def read_json(p, default):
    try:
        return json.load(open(p))
    except Exception:
        return default

def cfg_path(plat):
    return ROOT / f"configs/config.{plat}.json"

def prl_address():
    return read_json(cfg_path("vast"), {}).get("prl_address", "")

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

def pool_data():
    now = time.time()
    if _pool["data"] is not None and now - _pool["ts"] < POOL_TTL:
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


# ---------- Salad 实时 ----------
_salad = {"data": None, "ts": 0.0}
SALAD_TTL = 30.0

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
    """GPU 名归一化: 去掉 ' (xx GB)' 后缀并小写, 用于跨数据源匹配。"""
    return re.sub(r"\s*\(.*?\)\s*$", "", str(name or "")).strip().lower()

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

_gpucls = {"data": None, "ts": 0.0}

def salad_gpu_prices(base, org, key):
    """{uuid: {name, prices:{priority:price}}} ，缓存 10min。"""
    now = time.time()
    if _gpucls["data"] is not None and now - _gpucls["ts"] < 600:
        return _gpucls["data"]
    out = {}
    try:
        d = salad_get(f"{base}/organizations/{org}/gpu-classes", key)
        for g in (d.get("items") or []):
            pr = {p.get("priority"): p.get("price") for p in (g.get("prices") or [])}
            out[g.get("id")] = {"name": g.get("name"), "prices": pr}
    except Exception:
        pass
    _gpucls.update(data=out, ts=now)
    return out

def salad_live():
    now = time.time()
    if _salad["data"] is not None and now - _salad["ts"] < SALAD_TTL:
        return _salad["data"]
    res = {"instances": [], "counts": {}, "error": None, "price_label": None, "gpu_classes": []}
    scfg = read_config("salad").get("salad", {})
    key = read_env().get("SALAD_API_KEY") or os.environ.get("SALAD_API_KEY", "")
    org, proj = scfg.get("organization_name"), scfg.get("project_name")
    if not (key and org and proj and scfg.get("enabled")):
        res["error"] = "salad 未启用/未配置 key"
        _salad.update(data=res, ts=now)
        return res
    base = str(scfg.get("base_url", "https://api.salad.com/api/public")).rstrip("/")
    pre = f"{base}/organizations/{org}/projects/{proj}/containers"
    names = scfg.get("include_container_groups") or []
    try:
        gp = salad_gpu_prices(base, org, key)
        if not names:
            d = salad_get(pre, key)
            names = [g.get("name") for g in (d.get("items") or [])]
        watch = read_state("salad").get("salad_instance_watch") or {}
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
        def pgpu(w):
            gi = (w or {}).get("gpu_info") or []
            return str((gi[0] if gi else {}).get("name") or "").replace("NVIDIA GeForce ", "").strip()
        def phr(w):
            gi = (w or {}).get("gpu_info") or []
            return round(sum(hashrate_th(g.get("hashrate")) for g in gi), 2) if gi else None
        prices = []
        for nm in names:
            prio = "medium"
            label = None
            try:
                g = salad_get(f"{pre}/{urllib.parse.quote(str(nm))}", key)
                res["counts"][nm] = (g.get("current_state") or {}).get("instance_status_counts") or {}
                prio = g.get("priority") or "medium"
                cls = ((g.get("container") or {}).get("resources") or {}).get("gpu_classes") or []
                for c in cls:
                    nm = gp.get(c, {}).get("name")
                    if nm and nm not in res["gpu_classes"]:
                        res["gpu_classes"].append(nm)
                ps = [float(gp.get(c, {}).get("prices", {}).get(prio)) for c in cls
                      if gp.get(c, {}).get("prices", {}).get(prio) is not None]
                if ps:
                    lo, hi = min(ps), max(ps)
                    label = f"${lo:.3f}/h" if abs(lo - hi) < 1e-9 else f"${lo:.3f}–{hi:.3f}/h"
                    prices += ps
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
                res["error"] = f"instances: {type(ie).__name__}: {ie}"
            for inst in insts:
                iid = str(inst.get("instance_id") or inst.get("id") or "")
                mid = str(inst.get("machine_id") or "")
                w = watch.get(f"{nm}:{iid}") or {}
                pw = pool_match(mid)
                gpu = pgpu(pw) or (w.get("gpu") or "").strip() or "?"
                hr = w.get("last_hashrate_th")
                if hr is None:
                    hr = phr(pw)
                res["instances"].append({"id": iid, "machine_id": mid, "gpu": gpu, "group": nm,
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
                    res["instances"].append({"id": mid, "machine_id": mid, "gpu": igpu,
                                             "group": nm, "state": "running", "started_epoch": None,
                                             "price": inst_price_num(igpu),
                                             "price_label": inst_price(igpu), "hashrate_th": phr(w)})
        if prices:
            lo, hi = min(prices), max(prices)
            res["price_label"] = f"${lo:.3f}/h" if abs(lo - hi) < 1e-9 else f"${lo:.3f}–{hi:.3f}/h"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    _salad.update(data=res, ts=now)
    return res

def _http_json(method, url, headers, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = dict(headers); h.setdefault("User-Agent", "sniper-dashboard/1.0")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

_bal = {}  # plat -> (value|None, ts)
BAL_TTL = 60.0

def platform_balance(plat):
    """账户余额(USD)。Vast=credit, RunPod=clientBalance; TensorDock/Salad 无可用 API → None。"""
    now = time.time()
    c = _bal.get(plat)
    if c and now - c[1] < BAL_TTL:
        return c[0]
    val = None
    env = read_env()
    try:
        if plat == "vast":
            k = env.get("VAST_API_KEY") or os.environ.get("VAST_API_KEY", "")
            if k:
                d = _http_json("GET", "https://console.vast.ai/api/v0/users/current/", {"Authorization": "Bearer " + k})
                val = d.get("credit")
        elif plat == "runpod":
            k = env.get("RUNPOD_API_KEY") or os.environ.get("RUNPOD_API_KEY", "")
            if k:
                d = _http_json("POST", "https://api.runpod.io/graphql",
                               {"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                               body={"query": "query{myself{clientBalance}}"})
                val = ((d.get("data") or {}).get("myself") or {}).get("clientBalance")
        if val is not None:
            val = round(float(val), 2)
    except Exception:
        val = None
    _bal[plat] = (val, now)
    return val

def active_rentals(plat):
    st = read_state(plat)
    out = []
    if plat == "salad":
        for i in salad_live().get("instances", []):
            out.append({"id": i["id"], "gpu": i.get("gpu") or "?", "price": i.get("price"),
                        "price_label": i.get("price_label"),
                        "hashrate_th": i.get("hashrate_th"),
                        "created_epoch": i.get("started_epoch"),
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
        for plat in ["vast", "runpod", "tensordock", "salad"]:
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
        time.sleep(60)


# ---------- 总览数据 ----------
def build_summary():
    pool = pool_data()
    workers = pool.get("connected_workers", []) if isinstance(pool, dict) else []
    total_th, wlist = 0.0, []
    for w in workers:
        wth = sum(hashrate_th(g.get("hashrate")) for g in (w.get("gpu_info") or []))
        total_th += wth
        wlist.append({"name": w.get("worker_name"), "th": round(wth, 2), "ip": w.get("ip"),
                      "gpus": [g.get("name") for g in (w.get("gpu_info") or [])]})
    per_plat, running = {}, 0
    for plat in PLATFORMS:
        n = len(active_rentals(plat))
        per_plat[plat] = n
        running += n
    stats = read_json(STATS_PATH, {})
    pending = recent = 0.0
    if isinstance(pool, dict):
        pending = float((pool.get("pending_rewards") or {}).get("total_pending") or 0)
        recent = sum(float(t.get("amount") or 0) for t in (pool.get("balance_transactions") or []))
    return {
        "wallet": prl_address(),
        "running_machines": running,
        "running_by_platform": per_plat,
        "total_hashrate_th": round(total_th, 2),
        "workers": wlist,
        "cumulative_rent_usd": round(float(stats.get("cumulative_usd", 0.0)), 4),
        "current_hourly_usd": round(float(stats.get("current_hourly_usd", 0.0)), 4),
        "coins_pending": round(pending, 4),
        "coins_recent_settled": round(recent, 4),
        "pool_error": pool.get("_error") if isinstance(pool, dict) else None,
        "ts": int(time.time()),
    }

def build_rentals():
    now = time.time()
    res = {}
    for plat in PLATFORMS:
        cfg = read_config(plat).get(plat, {})
        items = []
        for r in active_rentals(plat):
            dur = int(now - float(r["created_epoch"])) if r.get("created_epoch") else None
            d = dict(r)
            d["duration_seconds"] = dur
            items.append(d)
        res[plat] = {
            "enabled": cfg.get("enabled"),
            "create_enabled": cfg.get("create_enabled"),
            "rent_paused": rent_paused(plat),
            "process_running": pid_for(plat) is not None,
            "thresholds": cfg.get("thresholds"),
            "min_hashrate_th": cfg.get("min_hashrate_th"),
            "machines": items,
        }
        bal = platform_balance(plat)
        burn = sum(float(m.get("price") or 0) for m in items)
        res[plat]["balance"] = bal
        res[plat]["burn_hourly"] = round(burn, 4)
        res[plat]["hours_left"] = round(bal / burn, 1) if (bal is not None and burn > 0) else None
        if plat == "salad":
            sl = salad_live()
            cnts = {}
            for c in (sl.get("counts") or {}).values():
                for k, v in (c or {}).items():
                    cnts[k] = cnts.get(k, 0) + (v or 0)
            res[plat]["salad_status"] = cnts
            res[plat]["salad_error"] = sl.get("error")
            res[plat]["salad_gpu_classes"] = sl.get("gpu_classes") or []
    return res

def build_config():
    env = read_env()
    res = {}
    for plat in PLATFORMS:
        kn = KEYNAME[plat]
        v = env.get(kn, "")
        is_set = bool(v) and not v.startswith("replace_with")
        res[plat] = {
            "key_name": kn,
            "key_set": is_set,
            "key_mask": ("…" + v[-4:]) if (is_set and len(v) >= 4) else ("已设置" if is_set else ""),
            "process_running": pid_for(plat) is not None,
            "rent_paused": rent_paused(plat),
        }
    return res


# ---------- 配置编辑 ----------
def gpu_rows(sub):
    th = sub.get("thresholds") or {}
    mh = sub.get("min_hashrate_th") or {}
    names = []
    for k in list(th) + list(mh):
        if "GeForce" in k or k in names:
            continue
        names.append(k)
    return [{"gpu": n, "max_price": th.get(n), "min_hashrate": mh.get(n)} for n in names]

def build_full_config():
    env = read_env()
    base = read_config("vast")
    common = {k: base.get(k) for k in COMMON_KEYS}
    plats = {}
    for plat in PLATFORMS:
        cfg = read_config(plat)
        sub = cfg.get(plat, {}) or {}
        kn = KEYNAME[plat]
        v = env.get(kn, "")
        is_set = bool(v) and not v.startswith("replace_with")
        spec = []
        for key, typ in SPECIFIC[plat]:
            spec.append({"key": key, "type": typ, "value": sub.get(key)})
        plats[plat] = {
            "enabled": bool(sub.get("enabled")),
            "has_create": plat in HAS_CREATE,
            "create_enabled": bool(sub.get("create_enabled")),
            "min_th_per_usd_hour": sub.get("min_th_per_usd_hour"),
            "gpus": gpu_rows(sub),
            "specific": spec,
            "key_name": kn,
            "key_set": is_set,
            "key_mask": ("…" + v[-4:]) if (is_set and len(v) >= 4) else "",
            "process_running": pid_for(plat) is not None,
            "rent_paused": rent_paused(plat),
            "raw": json.dumps(cfg, ensure_ascii=False, indent=2),
        }
    return {"common": common, "platforms": plats}

def backup_and_write(path, obj):
    try:
        if path.exists():
            (path.parent / (path.name + ".bak")).write_text(path.read_text())
    except Exception:
        pass
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

def save_platform_cfg(plat, patch):
    if plat not in PLATFORMS or not isinstance(patch, dict):
        return {"error": "参数无效"}
    p = cfg_path(plat)
    cfg = read_json(p, {})
    sub = cfg.get(plat, {}) or {}
    sub.update(patch)
    cfg[plat] = sub
    backup_and_write(p, cfg)
    return {"ok": True, "platform": plat}

def save_common_cfg(data):
    if not isinstance(data, dict):
        return {"error": "参数无效"}
    data = {k: v for k, v in data.items() if k in COMMON_KEYS}
    for plat in PLATFORMS:
        p = cfg_path(plat)
        cfg = read_json(p, {})
        cfg.update(data)
        backup_and_write(p, cfg)
    return {"ok": True, "written": len(PLATFORMS)}

def save_raw_cfg(plat, raw):
    if plat not in PLATFORMS:
        return {"error": "平台无效"}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"error": "顶层必须是 JSON 对象"}
    except Exception as e:
        return {"error": f"JSON 非法: {e}"}
    backup_and_write(cfg_path(plat), obj)
    return {"ok": True, "platform": plat}

def launch_platform(plat):
    env = dict(os.environ)
    for k, v in read_env().items():
        env[k] = v
    env["SNIPER_LOG_PATH"] = f"logs/{plat}.log"
    env["SNIPER_STATE_PATH"] = f"state.{plat}.json"
    try:
        logf = open(ROOT / f"logs/{plat}.log", "a")
        subprocess.Popen(["python3", "sniper.py", "--config", f"configs/config.{plat}.json", "--live"],
                         cwd=str(ROOT), env=env, stdout=logf, stderr=logf, start_new_session=True)
        return True
    except Exception:
        return False

def restart_platform(plat):
    if plat not in PLATFORMS:
        return {"error": "平台无效"}
    pid = pid_for(plat)
    if pid:
        try:
            subprocess.run(["kill", pid])
        except Exception:
            pass
        time.sleep(2)
    ok = launch_platform(plat)
    time.sleep(1.5)
    return {"ok": ok, "platform": plat, "process_running": pid_for(plat) is not None}

def do_rent_toggle(plat, paused):
    CONTROL_DIR.mkdir(exist_ok=True)
    flag = CONTROL_DIR / f"{plat}.rent-paused"
    launched = False
    if paused:
        flag.touch()
    else:
        if flag.exists():
            flag.unlink()
        if pid_for(plat) is None:
            launched = launch_platform(plat)
            time.sleep(1.5)
    return {"ok": True, "platform": plat, "rent_paused": flag.exists(),
            "process_running": pid_for(plat) is not None, "launched": launched}

def do_terminate(plat, mid, group=None):
    if not mid:
        return {"error": "缺少实例 id"}
    try:
        import sniper as S
        if plat == "vast":
            r = S.destroy_vast_instance(mid)
        elif plat == "runpod":
            r = S.delete_runpod_pod(mid)
        elif plat == "tensordock":
            r = S.delete_tensordock_instance(read_config("tensordock"), mid)
        elif plat == "salad":
            r = S.reallocate_salad_instance(read_config("salad"), group or "", mid)
        else:
            return {"error": "平台无效"}
        return {"ok": True, "platform": plat, "id": mid, "result": r}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


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
            if path == "/api/summary":
                return self._send(200, build_summary())
            if path == "/api/rentals":
                return self._send(200, build_rentals())
            # ↓ 以下仅管理员;访客(guest)只能看总览数据与工具链接
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
                if plat not in PLATFORMS:
                    return self._send(400, {"error": "平台无效"})
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
            if plat not in KEYNAME or not value:
                return self._send(400, {"error": "参数无效"})
            set_env_key(KEYNAME[plat], value)
            return self._send(200, {"ok": True, "platform": plat})
        if path == "/api/rent-toggle":
            plat = str(data.get("platform", ""))
            if plat not in PLATFORMS:
                return self._send(400, {"error": "平台无效"})
            return self._send(200, do_rent_toggle(plat, bool(data.get("paused"))))
        if path == "/api/terminate":
            plat = str(data.get("platform", ""))
            if plat not in PLATFORMS:
                return self._send(400, {"error": "平台无效"})
            return self._send(200, do_terminate(plat, str(data.get("id", "")), str(data.get("group", "")) or None))
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
        return self._send(404, {"error": "not found"})


HTML = r"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>今晚挖珍珠 · Pearl Sniper</title>
<meta name=theme-color content="#0a0e17">
<link rel="icon" href="data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2064%2064'><defs><radialGradient%20id='g'%20cx='37%25'%20cy='31%25'%20r='78%25'><stop%20offset='0%25'%20stop-color='%23f2fffc'/><stop%20offset='17%25'%20stop-color='%238ff3e6'/><stop%20offset='42%25'%20stop-color='%233fe0c5'/><stop%20offset='71%25'%20stop-color='%231aa6cf'/><stop%20offset='100%25'%20stop-color='%23083f57'/></radialGradient><radialGradient%20id='h'%20cx='50%25'%20cy='50%25'%20r='50%25'><stop%20offset='0%25'%20stop-color='%233fe0c5'%20stop-opacity='0.55'/><stop%20offset='100%25'%20stop-color='%233fe0c5'%20stop-opacity='0'/></radialGradient></defs><rect%20width='64'%20height='64'%20rx='15'%20fill='%230a0e17'/><circle%20cx='32'%20cy='32'%20r='27'%20fill='url(%23h)'/><circle%20cx='32'%20cy='32'%20r='17'%20fill='url(%23g)'/><ellipse%20cx='25.5'%20cy='24'%20rx='6.5'%20ry='4.6'%20fill='%23ffffff'%20opacity='0.92'/></svg>">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Roboto+Mono:wght@400;500;600;700&display=swap');
:root{--bg:#0a0e17;--bg2:#0f1623;--card:#141b2b;--card2:#172033;--bd:#23304a;--bd2:#33415f;
--tx:#c4cde2;--hi:#eef2fb;--mut:#79839c;--g1:#5a8dff;--g2:#3fe0c5;
--acc:#3fe0c5;--acc2:rgba(63,224,197,.12);--ok:#3fe0c5;--okbg:rgba(63,224,197,.12);
--warn:#ffb259;--warnbg:rgba(255,178,89,.13);--bad:#ff7a7a;--badbg:rgba(255,122,122,.13);
--mono:'Roboto Mono',ui-monospace,"SF Mono",Menlo,monospace}
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
.req{color:var(--bad);font-size:10px;border:1px solid rgba(255,122,122,.4);background:var(--badbg);border-radius:5px;padding:1px 6px;letter-spacing:.5px}
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
.side{width:210px;flex-shrink:0;background:rgba(15,22,35,.55);border-right:1px solid var(--bd);padding:20px 14px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.sbrand{display:flex;align-items:center;gap:11px;margin-bottom:24px}
.sbrand .bt{font-weight:800;font-size:15.5px;line-height:1.2;color:var(--hi)}
.sbrand .bt small{display:block;font-size:9.5px;letter-spacing:1.8px;color:var(--mut);font-weight:600;margin-top:4px;font-family:var(--mono)}
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
.ni.on{background:linear-gradient(92deg,rgba(90,141,255,.2),rgba(63,224,197,.16));color:var(--hi);box-shadow:inset 2px 0 0 var(--g2)}
.ni.sub{padding-left:24px;font-size:12.5px}
.nigrp{margin:16px 0 5px;padding:0 13px;font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:var(--mut);font-weight:700}
.sfoot{margin-top:auto;color:var(--mut);font-size:11px;font-family:var(--mono);padding:12px 13px 0;border-top:1px solid var(--bd)}
main{flex:1;min-width:0;padding:26px 32px;display:flex;justify-content:center}
.inner{width:100%;max-width:1040px}
.b-mini{padding:7px 12px;font-size:11.5px}
.suser{color:var(--tx);font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:7px;margin-bottom:8px}
.suser .dot{width:7px;height:7px;border-radius:50%;background:var(--g2);box-shadow:0 0 8px var(--g2);flex-shrink:0}
.lgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));gap:14px}
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
<div id=login style=display:none><div class=box>
<div style="display:flex;align-items:center;gap:18px;margin-bottom:14px">
<span class=orb style="width:42px;height:42px"></span>
<div><div class=logo>// PEARL_SNIPER v1</div><h2 style="margin:2px 0 0">今晚挖<i class=pg>珍珠</i></h2></div></div>
<div class=sub>PEARL SNIPER DASHBOARD</div>
<input id=pw type=password placeholder="ACCESS PASSWORD" onkeydown="if(event.key=='Enter')login()">
<div class=err id=lerr></div>
<button class=b-acc style="width:100%;margin-top:14px" onclick=login()>登录 / LOGIN</button>
<div class=peek onclick=guestLogin()>👁 偷窥模式 · 仅看总览 / PEEK MODE</div></div></div>

<div class=app>
<aside class=side>
<div class=sbrand><span class=orb style="width:26px;height:26px"></span><span class=bt>今晚挖<i class=pg>珍珠</i><small>PEARL SNIPER v1</small></span></div>
<nav class=nav>
<div class="ni on" data-nav=ov onclick="nav('ov')">总览</div>
<div class="ni" data-nav=lk onclick="nav('lk')">工具链接</div>
<div class="nigrp adm">配置</div>
<div class="ni sub adm" data-nav=cf:common onclick="nav('cf:common')">公共配置</div>
<div class="ni sub adm" data-nav=cf:vast onclick="nav('cf:vast')">VAST</div>
<div class="ni sub adm" data-nav=cf:runpod onclick="nav('cf:runpod')">RUNPOD</div>
<div class="ni sub adm" data-nav=cf:tensordock onclick="nav('cf:tensordock')">TENSORDOCK</div>
<div class="ni sub adm" data-nav=cf:salad onclick="nav('cf:salad')">SALAD</div>
</nav>
<div class=sfoot><div class=suser><span class=dot></span><span id=uname>admin</span></div><div id=clock></div>
<div class=logout onclick=logout()>⏏ 退出登录</div></div>
</aside>
<main><div class=inner><div id=ov></div><div id=lk style=display:none></div><div id=cf style=display:none></div></div></main>
</div>
<div class=toast id=toast></div>

<script>
let view='ov',subtab='common',ROLE='admin';
function applyRole(){let adm=ROLE=='admin';
document.querySelectorAll('.adm').forEach(e=>e.style.display=adm?'':'none');
let u=document.getElementById('uname');if(u)u.textContent=adm?'admin':'访客 GUEST';
let dot=document.querySelector('.suser .dot');if(dot)dot.style.background=adm?'var(--g2)':'var(--warn)';
if(!adm&&view=='cf'){nav('ov');}}
function toast(m){let t=document.getElementById('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',2600);}
function nav(t){if(t=='ov'){view='ov';}else if(t=='lk'){view='lk';}else{view='cf';subtab=t.split(':')[1];}
document.querySelectorAll('.ni').forEach(e=>e.classList.toggle('on',e.dataset.nav==t));
document.getElementById('ov').style.display=view=='ov'?'':'none';
document.getElementById('lk').style.display=view=='lk'?'':'none';
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
function esc(s){return (s==null?'':''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function dur(s){if(s==null)return '-';let h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h+'h'+m+'m';}
function fnum(n,d){if(n==null)return '-';n=Number(n);if(Math.abs(n)<1e-9)n=0;return n.toLocaleString(undefined,{maximumFractionDigits:d==null?2:d});}

async function renderOverview(){let d,r;try{d=await api('/api/summary');r=await api('/api/rentals')}catch(e){return}
let acct='https://pearlhash.xyz/account/'+encodeURIComponent(d.wallet);
let pe=d.pool_error?`<div class=muted style="color:var(--warn);margin-top:10px">POOL_API: ${esc(d.pool_error)}</div>`:'';
let bp=Object.entries(d.running_by_platform).map(([k,v])=>`${k} ${v}`).join('  ·  ');
let wk=(d.workers||[]).map(w=>`<tr><td>${esc(w.name)}</td><td>${esc((w.gpus||[]).join(', '))}</td><td><b style=color:var(--acc)>${fnum(w.th)}</b> TH/s</td><td>${esc(w.ip)}</td></tr>`).join('')||'<tr><td colspan=4 class=muted>矿池暂无在挖 worker</td></tr>';
let plat='';for(const p of ['vast','runpod','tensordock','salad']){const v=r[p];
let badges=`<span class="pill ${v.process_running?'ok':'bad'}">${v.process_running?'RUNNING':'STOPPED'}</span>`+(v.rent_paused?'<span class="pill warn">RENT PAUSED</span>':'');
let bh;if(v.balance!=null){let t=(v.hours_left!=null)?('约 '+fnum(v.hours_left,1)+'h 花完'):(v.burn_hourly>0?'':'当前无消耗');bh=`<span class=bal>余额 $${fnum(v.balance,2)}${t?' · '+t:''}</span>`;}else{bh='<span class=bal>余额 —</span>';}
let sstat='';if(p=='salad'){let s=v.salad_status||{};let pr=[];if(s.running_count!=null)pr.push('运行 '+s.running_count);if(s.allocating_count)pr.push('分配中 '+s.allocating_count);let gc=(v.salad_gpu_classes||[]).join(' / ');let serr=(v.salad_error&&!(v.machines||[]).length)?' · '+esc(v.salad_error):'';sstat=`<div class=muted style=margin-bottom:9px>SALAD 实时 · ${pr.join(' · ')||'-'}${gc?' · GPU 档 '+esc(gc):''}${serr}</div>`;}
let rows=(v.machines||[]).map(m=>{let a=(ROLE=='admin'&&m.id)?`<button class=b-bad onclick="term('${p}','${esc(m.id)}','${esc(m.group||'')}')">关闭</button>`:'';
let price=m.price_label?esc(m.price_label):(m.price==null?'-':'$'+fnum(m.price,3)+'/h');
let gpu=(m.gpu&&m.gpu!='?')?esc(m.gpu):'<span class=muted>—</span>';
return `<tr><td>${esc(m.id)}</td><td>${gpu}</td><td>${price}</td><td>${dur(m.duration_seconds)}</td><td>${m.hashrate_th==null?'<span class=muted>—</span>':fnum(m.hashrate_th)+' TH/s'}</td><td>${a}</td></tr>`;}).join('')||`<tr><td colspan=6 class=muted>无在跑机器</td></tr>`;
plat+=`<div class=platbox><div class=top><b>${p.toUpperCase()}</b>${badges}${bh}</div>${sstat}
<table><tr><th>实例</th><th>GPU</th><th>单价</th><th>时长</th><th>算力</th><th></th></tr>${rows}</table></div>`;}
document.getElementById('ov').innerHTML=`
<div class="card wallet">
<div style=min-width:0><div class=k>WALLET · 钱包地址</div><div class=addr>${esc(d.wallet)}</div></div>
<div class=row style=flex-shrink:0;gap:8px>
<button class=b-mini onclick="copyAddr('${esc(d.wallet)}')">复制</button>
<div class=go onclick="window.open('${acct}','_blank')">ACCOUNT →</div></div></div>
<div class=cards>
<div class=card><div class=k>在跑机器</div><div class=v>${d.running_machines}</div><div class=sub>${esc(bp)}</div></div>
<div class=card><div class=k>总算力 矿池实测</div><div class=v>${fnum(d.total_hashrate_th)} <small>TH/s</small></div></div>
<div class=card><div class=k>累计租金</div><div class=v>$${fnum(d.cumulative_rent_usd)}</div><div class=sub>$${fnum(d.current_hourly_usd)}/h · 自看板起算</div></div>
<div class=card><div class=k>待结算 pearl</div><div class=v style=color:var(--acc)>${fnum(d.coins_pending,4)}</div></div>
<div class=card><div class=k>近期已结算 pearl</div><div class=v>${fnum(d.coins_recent_settled,4)}</div></div>
</div>${pe}
<div class=sec><div class=lbl>矿池在挖 WORKER</div><table><tr><th>Worker</th><th>GPU</th><th>算力</th><th>IP</th></tr>${wk}</table></div>
<div class=sec><div class=lbl>各平台租用情况</div>${plat}</div>`;}

let CFG=null;
async function renderConfigTab(){let d;try{d=await api('/api/full-config')}catch(e){return}CFG=d;
document.getElementById('cf').innerHTML=subtab=='common'?commonHtml(d):platformHtml(d.platforms[subtab],subtab);}
function commonHtml(d){let c=d.common;
let cf=(k,label,req,ph)=>`<div class=fld>${label}${req?' <span class=req>必填</span>':''}</div><input id="cm_${k}" value="${esc(c[k]==null?'':c[k])}" placeholder="${ph||''}">`;
return `<div class=lbl>公共配置 · COMMON</div>
<div class=platbox><div class=top><b>COMMON</b><span class=muted>改这里会写入全部 4 个 config</span></div>
<div class=grid2>
${cf('prl_address','钱包地址 prl_address',1,'你的 $pearl 钱包, 否则挖给别人')}
${cf('prl_host','矿池 prl_host',0,'84.32.220.219:9000')}
${cf('worker_prefix','worker 前缀',0,'auto')}
${cf('image','矿机镜像 image',0,'docker.io/kuzigmgm/pearl-miner:v11')}
${cf('max_active_instances','最多同时租 (台)',0,'1')}
${cf('max_total_hourly_usd','总时租上限 ($/h)',0,'1.0')}
${cf('alert_url','告警 URL (可空)',0,'ntfy 等')}
</div><div class=row style=margin-top:12px><button class=b-acc onclick=saveCommon()>保存公共配置</button>
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
return `<div class=lbl>${p.toUpperCase()} · 平台配置</div>
<div class=platbox id=box_${p}><div class=top><b>${p.toUpperCase()}</b>${proc}</div>
<div class=grid2>
<div class=fld>启用 enabled</div><div><input type=checkbox id="en_${p}" ${v.enabled?'checked':''}></div>
${v.has_create?`<div class=fld>自动建机 create_enabled</div><div><input type=checkbox id="ce_${p}" ${v.create_enabled?'checked':''}></div>`:''}
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
async function loadLog(p){let n=document.getElementById('loglines_'+p).value;let pre=document.getElementById('log_'+p);
pre.style.display='';pre.textContent='加载中…';
try{let r=await api('/api/logs?platform='+p+'&lines='+n);pre.textContent=r.log||'(空)';pre.scrollTop=pre.scrollHeight;}catch(e){pre.textContent='加载失败';}}

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
let r=await api('/api/save-common',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:data})});
toast(r.error?('失败: '+r.error):'公共配置已写入全部 config, 各平台点重启生效');}
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
async function term(p,id,group){let label=p=='salad'?'迁移(reallocate)':'关闭并销毁';
if(!confirm('确定要'+label+'这台机器吗?\n'+p+' · '+id))return;
let r=await api('/api/terminate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p,id:id,group:group})});
toast(r.error?('失败: '+r.error):'已执行 '+id);renderOverview();}
const LINKS=[
{t:'官网',i:'🌐',items:[['Pearl Research','https://pearlresearch.ai/']]},
{t:'区块浏览器',i:'🔎',items:[['Explorer','https://explorer.pearlresearch.ai/']]},
{t:'钱包',i:'👛',items:[['Compute Wallet','https://compute.pearlresearch.ai/wallet']]},
{t:'矿池',i:'⛏️',items:[['PearlHash','http://pearlhash.xyz'],['AlphaPool','https://pearl.alphapool.tech/']]},
{t:'租卡平台',i:'🖥️',items:[['Salad','https://portal.salad.com/'],['RunPod','https://runpod.io?ref=9hx2ahkb'],['TensorDock','https://dashboard.tensordock.com/'],['Vast.ai','https://cloud.vast.ai/']]},
{t:'收益计算器',i:'🧮',items:[['Akakay 计算器','https://pearl.akakay.com/'],['Pearl Dashboard','https://pearl-dashboard-pearl.vercel.app/']]},
];
function dom(u){try{return new URL(u).host}catch(e){return u}}
function renderLinks(){let cards=LINKS.map(c=>`<div class=lcard><h3>${c.i} ${esc(c.t)}</h3>`+
c.items.map(it=>`<a class=linkitem href="${esc(it[1])}" target=_blank rel=noopener><span class=nm>${esc(it[0])}</span><span class=d>${esc(dom(it[1]))} ↗</span></a>`).join('')+`</div>`).join('');
document.getElementById('lk').innerHTML=`<div class=lbl>工具链接 · TOOLS</div><div class=lgrid>${cards}</div>`;}
function refresh(){if(view=='ov')renderOverview();else if(view=='lk')renderLinks();else renderConfigTab();}
setInterval(()=>{let c=document.getElementById('clock');if(c)c.textContent=new Date().toLocaleTimeString();},1000);
setInterval(()=>{if(view=='ov')renderOverview();},10000);initRole();refresh();
</script></body></html>"""


def main():
    CONTROL_DIR.mkdir(exist_ok=True)
    threading.Thread(target=spend_loop, daemon=True).start()
    port = int(CONF.get("port", 8787))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"pearl dashboard on http://0.0.0.0:{port}  (user={CONF.get('user','admin')})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
