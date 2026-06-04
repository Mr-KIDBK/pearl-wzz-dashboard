#!/usr/bin/env python3
import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock


ROOT = Path(__file__).resolve().parent
STATE_PATH = Path(os.environ.get("SNIPER_STATE_PATH") or (ROOT / "state.json"))
LOG_PATH = Path(os.environ.get("SNIPER_LOG_PATH") or (ROOT / "sniper.log"))


def renting_paused(provider):
    """看板暂停租用: 存在 control/<provider>.rent-paused 文件时, 只停租用/迁移, 监控照常。"""
    try:
        return (ROOT / "control" / f"{provider}.rent-paused").exists()
    except Exception:
        return False


def now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def epoch_now():
    return time.time()


def log(message):
    line = f"{now()} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(config, title, message, priority="default", tags=None):
    url = str(config.get("alert_url") or "").strip()
    if not url:
        return
    headers = {
        "Title": str(title),
        "Priority": str(priority),
        "Tags": ",".join(tags or []),
        "User-Agent": "gpu-sniper/1.0",
    }
    data = str(message).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        log(f"Notify failed: {type(exc).__name__}: {exc}")


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=True, sort_keys=True)
    tmp.replace(path)


def request_json(method, url, headers=None, body=None, timeout=30, retries=None):
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req_headers.setdefault("Accept", "application/json")
    req_headers.setdefault("User-Agent", "gpu-sniper/1.0")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    # 仅对幂等的 GET 自动重试瞬时网络错误（SSL EOF / 连接 reset / 超时）。
    # POST/PUT/DELETE 绝不重试：响应丢了但服务端可能已执行，重试会重复建机/扣费。
    if retries is None:
        retries = 2 if str(method).upper() == "GET" else 0
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError:
            raise  # 真实 HTTP 响应（4xx/5xx），不属于瞬时网络错误，不重试
        except (urllib.error.URLError, OSError):
            attempt += 1
            if attempt > retries:
                raise
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 4))


def request_graphql(url, token, query, variables=None, timeout=30):
    body = {"query": query, "variables": variables or {}}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return request_json("POST", url, headers, body, timeout=timeout)


def normalize_gpu(name):
    if not name:
        return ""
    text = re.sub(r"\s+", " ", name.upper()).replace("GEFORCE ", "")
    compact = re.sub(r"[^A-Z0-9]+", "", text)
    if "5090" in compact:
        return "RTX 5090"
    if "4090" in compact:
        return "RTX 4090"
    if "3090TI" in compact:
        return "RTX 3090 Ti"
    if "3090" in compact:
        return "RTX 3090"
    if "3080TI" in compact:
        return "RTX 3080 Ti"
    if "3080" in compact:
        return "RTX 3080"
    if "5060TI" in compact:
        return "RTX 5060 Ti"
    for token in ("RTX 5090", "RTX 4090", "RTX 3090 TI", "RTX 3090", "RTX 3080 TI", "RTX 3080", "RTX 5060 TI"):
        if token in text:
            return token.title().replace("Ti", "Ti").replace("Rtx", "RTX")
    return name


def gpu_matches(name, wanted):
    return normalize_gpu(name) == normalize_gpu(wanted)


def threshold_for(name, thresholds):
    normalized = normalize_gpu(name)
    for key, price in thresholds.items():
        if normalize_gpu(key) == normalized:
            return float(price)
    return None


def gpu_map_value(name, mapping, default=None):
    normalized = normalize_gpu(name)
    for key, value in (mapping or {}).items():
        if normalize_gpu(key) == normalized:
            return value
    return default


def parse_latest_hashrate(log_text):
    latest = None
    for line in str(log_text or "").splitlines():
        match = re.search(r"Hashrate Total\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?H)/s", line, re.I)
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            multiplier = {
                "H": 1 / 1_000_000_000_000,
                "KH": 1 / 1_000_000_000,
                "MH": 1 / 1_000_000,
                "GH": 1 / 1_000,
                "TH": 1,
                "PH": 1_000,
                "EH": 1_000_000,
            }.get(unit, 1)
            latest = value * multiplier
            continue
        match = re.search(r"(?:hashrate_th_s|share_equiv_th_s)=([0-9]+(?:\.[0-9]+)?)", line, re.I)
        if match:
            latest = float(match.group(1))
    return latest


def parse_hashrate_text(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?H)/s", text, re.I)
    if not match:
        return 0.0
    numeric = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {
        "H": 1 / 1_000_000_000_000,
        "KH": 1 / 1_000_000_000,
        "MH": 1 / 1_000_000,
        "GH": 1 / 1_000,
        "TH": 1,
        "PH": 1_000,
        "EH": 1_000_000,
    }.get(unit, 1)
    return numeric * multiplier


def parse_log_gpu_name(log_text):
    text = str(log_text or "")
    match = re.search(r"RTX\s+5070\s+Ti|RTX\s+4070\s+Ti\s+Super|RTX\s+3090\s+Ti|RTX\s+3080\s+Ti|RTX\s+\d{4}", text, re.I)
    if not match:
        return ""
    return " ".join(match.group(0).upper().replace("RTX", "RTX ").split())


def hashrate_to_th(value):
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    # PearlHash API returns raw H/s.
    return numeric / 1_000_000_000_000


def pearl_worker_hashrates(config):
    address = str(config.get("prl_address") or "").strip()
    if not address:
        return {}
    url = f"https://pearlhash.xyz/api/account/{urllib.parse.quote(address)}"
    data = request_json("GET", url, timeout=20)
    workers = {}
    for worker in (data or {}).get("connected_workers", []):
        name = str(worker.get("worker_name") or "")
        total = 0.0
        for gpu in worker.get("gpu_info") or []:
            total += hashrate_to_th(gpu.get("hashrate"))
        if name:
            workers[name] = {
                "hashrate_th": total,
                "ip": worker.get("ip"),
                "version": worker.get("version"),
                "gpu_info": worker.get("gpu_info") or [],
            }
    return workers


def alphapool_worker_hashrates(config):
    address = str(config.get("prl_address") or "").strip()
    if not address:
        return {}
    cfg = config.get("salad", {})
    base = str(cfg.get("alphapool_api_base") or "https://pearl.alphapool.tech").rstrip("/")
    field = str(cfg.get("alphapool_hashrate_field") or "hashrate_live")
    url = f"{base}/api/miner/{urllib.parse.quote(address)}"
    data = request_json("GET", url, timeout=int(cfg.get("alphapool_api_timeout_seconds", 20)))
    workers = {}
    for worker in (data or {}).get("workers", []):
        name = str(worker.get("name") or "")
        if not name:
            continue
        workers[name] = {
            "hashrate_th": parse_hashrate_text(worker.get(field) or worker.get("hashrate_live") or worker.get("hashrate_1h") or worker.get("hashrate")),
            "hashrate_live_th": parse_hashrate_text(worker.get("hashrate_live")),
            "hashrate_1h_th": parse_hashrate_text(worker.get("hashrate_1h")),
            "hashrate_24h_th": parse_hashrate_text(worker.get("hashrate")),
            "online": bool(worker.get("online")),
            "time": worker.get("time"),
            "difficulty": worker.get("difficulty"),
        }
    return workers



def compact_location(offer):
    return str(offer.get("geolocation") or offer.get("location") or "").strip()


def is_preferred_location(offer, cfg):
    countries = [c.upper() for c in cfg.get("prefer_countries", [])]
    if not countries:
        return True
    loc = compact_location(offer).upper()
    preferred = any(loc.endswith(", " + c) or loc == c for c in countries)
    return preferred or bool(cfg.get("allow_other_countries", True))


def active_count(state):
    return len([x for x in state.get("rented", []) if x.get("active", True)])


def active_hourly(state):
    return sum(float(x.get("price", 0)) for x in state.get("rented", []) if x.get("active", True))


def active_count_excluding(state, provider):
    return len([x for x in state.get("rented", []) if x.get("active", True) and x.get("provider") != provider])


def active_hourly_excluding(state, provider):
    return sum(float(x.get("price", 0)) for x in state.get("rented", []) if x.get("active", True) and x.get("provider") != provider)


def already_seen(state, provider, external_id):
    key = f"{provider}:{external_id}"
    return key in state.get("seen", {})


def offer_machine_ids(offer):
    ids = []
    for key in (
        "machine_id",
        "machineId",
        "host_id",
        "hostId",
        "hostnode_id",
        "hostnodeId",
        "machine",
        "host",
        "node",
    ):
        value = offer.get(key) if isinstance(offer, dict) else None
        if isinstance(value, dict):
            value = value.get("id") or value.get("machine_id") or value.get("host_id")
        if value is not None and str(value).strip():
            ids.append(str(value).strip())
    return sorted(set(ids))


def is_blacklisted(state, provider, offer):
    blacklist = state.get("blacklist", {})
    offer_id = str(offer.get("id", ""))
    if f"{provider}:{offer_id}" in blacklist.get("offers", {}):
        return True
    for machine_id in offer_machine_ids(offer):
        if f"{provider}:{machine_id}" in blacklist.get("machines", {}):
            return True
    return False


def blacklist_offer(state, provider, offer_id, reason, details=None):
    state.setdefault("blacklist", {}).setdefault("offers", {})[f"{provider}:{offer_id}"] = {
        "time": now(),
        "reason": reason,
        "details": details or {},
    }


def blacklist_machine(state, provider, machine_id, reason, details=None):
    if not machine_id:
        return
    state.setdefault("blacklist", {}).setdefault("machines", {})[f"{provider}:{machine_id}"] = {
        "time": now(),
        "reason": reason,
        "details": details or {},
    }


def mark_seen(state, provider, external_id, details):
    state.setdefault("seen", {})[f"{provider}:{external_id}"] = {
        "time": now(),
        "details": details,
    }


def record_rent(state, provider, external_id, gpu, price, result):
    if isinstance(result, dict):
        result = {k: v for k, v in result.items() if "key" not in k.lower() and "token" not in k.lower()}
    contract_id = None
    if isinstance(result, dict):
        contract_id = result.get("new_contract") or result.get("id") or result.get("uuid") or result.get("instance_id") or result.get("instanceId")
        if not contract_id and isinstance(result.get("data"), dict):
            data = result["data"]
            attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
            contract_id = data.get("id") or attrs.get("id") or attrs.get("uuid") or attrs.get("instance_id") or attrs.get("instanceId")
    state.setdefault("rented", []).append({
        "time": now(),
        "created_epoch": epoch_now(),
        "provider": provider,
        "external_id": str(external_id),
        "contract_id": str(contract_id) if contract_id else None,
        "gpu": gpu,
        "price": float(price),
        "result": result,
        "active": True,
    })


def make_env(config, provider, gpu, external_id):
    safe_gpu = re.sub(r"[^A-Za-z0-9]+", "-", gpu).strip("-").lower()
    worker = f"{config.get('worker_prefix', 'auto')}-{provider}-{safe_gpu}-{external_id}"
    provider_cfg = config.get(provider, {}) if isinstance(config.get(provider, {}), dict) else {}
    price = float(provider_cfg.get("_current_price", 0) or 0)
    min_hashrate = gpu_map_value(gpu, provider_cfg.get("min_hashrate_th", {}), 0) or 0
    return {
        "PRL_ADDRESS": config["prl_address"],
        "PRL_HOST": config["prl_host"],
        "PRL_WORKER": worker[:63],
        "ALERT_URL": str(config.get("alert_url") or ""),
        "RENTAL_PRICE_USD_HOUR": str(price),
        "MIN_TH_PER_USD_HOUR": str(provider_cfg.get("min_th_per_usd_hour", 0)),
        "MIN_HASHRATE_TH": str(min_hashrate),
        "HASHRATE_WATCH_INTERVAL_SECONDS": str(provider_cfg.get("hashrate_watch_interval_seconds", 30)),
        "HASHRATE_ZERO_RECOVER_SECONDS": str(provider_cfg.get("hashrate_zero_recover_seconds", 120)),
        "LOW_EFFICIENCY_STOP_SECONDS": str(provider_cfg.get("low_efficiency_stop_seconds", 900)),
    }


def make_worker(config, provider, gpu, external_id):
    safe_gpu = re.sub(r"[^A-Za-z0-9]+", "-", gpu).strip("-").lower()
    return f"{config.get('worker_prefix', 'auto')}-{provider}-{safe_gpu}-{external_id}"[:63]


def find_vast_offers(config, state):
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        log("Vast skipped: VAST_API_KEY is not set")
        return []
    cfg = config["vast"]
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "limit": 500,
        "type": "on-demand",
        "rentable": {"eq": True},
        "rented": {"eq": False},
        "num_gpus": {"eq": 1},
        "reliability": {"gte": float(cfg.get("min_reliability", 0.95))},
        "dph_total": {"lte": float(cfg.get("max_offer_price_usd", 0.8))},
    }
    data = request_json("POST", "https://console.vast.ai/api/v0/bundles/", headers, body, timeout=45)
    offers = data.get("offers", []) if isinstance(data, dict) else []
    matches = []
    wanted_gpus = set(normalize_gpu(x) for x in cfg.get("thresholds", {}).keys())
    for offer in offers:
        if is_blacklisted(state, "vast", offer):
            continue
        gpu = offer.get("gpu_name") or ""
        if normalize_gpu(gpu) not in wanted_gpus:
            continue
        max_price = threshold_for(gpu, cfg.get("thresholds", {}))
        if max_price is None:
            continue
        price = float(offer.get("dph_total") or 999)
        if price < float(cfg.get("min_offer_price_usd", 0)):
            continue
        if price > max_price:
            continue
        if offer.get("gpu_frac") is not None and float(offer.get("gpu_frac") or 0) < float(cfg.get("min_gpu_frac", 1.0)):
            continue
        if float(offer.get("cpu_cores_effective") or 0) < float(cfg.get("min_cpu_cores_effective", 0)):
            continue
        if float(offer.get("cpu_ram") or 0) < float(cfg.get("min_cpu_ram_mb", 0)):
            continue
        if float(offer.get("disk_space") or 0) < float(cfg.get("min_disk_gb", 0)):
            continue
        if not is_preferred_location(offer, cfg):
            continue
        matches.append({
            "provider": "vast",
            "id": offer["id"],
            "gpu": gpu,
            "price": price,
            "location": compact_location(offer),
            "raw": offer,
        })
    return sorted(matches, key=lambda x: x["price"])


def rent_vast(config, match, state, live):
    offer_id = match["id"]
    if renting_paused("vast"):
        return False
    if already_seen(state, "vast", offer_id):
        return False
    if active_count(state) >= int(config.get("max_active_instances", 1)):
        log(f"Vast hit but max_active_instances reached: {match['gpu']} ${match['price']:.3f}/h offer={offer_id}")
        return False
    if active_hourly(state) + float(match["price"]) > float(config.get("max_total_hourly_usd", 0)):
        log(f"Vast hit but max_total_hourly_usd reached: {match['gpu']} ${match['price']:.3f}/h offer={offer_id}")
        return False
    log(f"Vast hit: {match['gpu']} ${match['price']:.3f}/h {match['location']} offer={offer_id}")
    if not live:
        log("Dry run: not renting Vast offer")
        return False
    mark_seen(state, "vast", offer_id, {"gpu": match["gpu"], "price": match["price"]})
    api_key = os.environ["VAST_API_KEY"]
    old_price = config["vast"].get("_current_price")
    config["vast"]["_current_price"] = match["price"]
    env = make_env(config, "vast", match["gpu"], offer_id)
    if old_price is None:
        config["vast"].pop("_current_price", None)
    else:
        config["vast"]["_current_price"] = old_price
    body = {
        "image": config["image"],
        "label": env["PRL_WORKER"],
        "disk": float(config["vast"].get("disk_gb", 20)),
        "runtype": "args",
        "target_state": "running",
        "cancel_unavail": True,
        "env": env,
    }
    try:
        result = request_json(
            "PUT",
            f"https://console.vast.ai/api/v0/asks/{offer_id}/",
            {"Authorization": f"Bearer {api_key}"},
            body,
            timeout=60,
        )
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            body_text = str(exc)
        log(f"Vast rent failed: offer={offer_id} HTTP {exc.code} {body_text[:500]}")
        if "no_such_ask" in body_text or "not available" in body_text.lower():
            blacklist_offer(state, "vast", offer_id, "offer_not_available", {"gpu": match["gpu"], "price": match["price"]})
        return False
    record_rent(state, "vast", offer_id, match["gpu"], match["price"], result)
    if isinstance(result, dict):
        log(f"Vast rent result: offer={offer_id} success={result.get('success')} contract={result.get('new_contract')}")
    else:
        log(f"Vast rent result: offer={offer_id} result={result}")
    notify(
        config,
        "Vast GPU rented",
        f"{match['gpu']} ${float(match['price']):.3f}/h {match['location']} offer={offer_id} contract={(result or {}).get('new_contract') if isinstance(result, dict) else ''}",
        priority="high",
        tags=["white_check_mark", "vast"],
    )
    return True


def run_vast_cycle(config, state, live):
    if not config.get("vast", {}).get("enabled", False):
        return
    if live:
        reconcile_vast_instances(config, state)
    for match in find_vast_offers(config, state):
        if rent_vast(config, match, state, live):
            break


def list_vast_instances():
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        return []
    data = request_json(
        "GET",
        "https://console.vast.ai/api/v1/instances/",
        {"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if isinstance(data, dict):
        return data.get("instances") or data.get("items") or []
    if isinstance(data, list):
        return data
    return []


def destroy_vast_instance(instance_id):
    api_key = os.environ["VAST_API_KEY"]
    return request_json(
        "DELETE",
        f"https://console.vast.ai/api/v0/instances/{instance_id}/",
        {"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )


def request_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "gpu-sniper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def request_vast_instance_logs(instance_id, tail=300):
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        return ""
    data = request_json(
        "PUT",
        f"https://console.vast.ai/api/v0/instances/request_logs/{instance_id}",
        {"Authorization": f"Bearer {api_key}"},
        {"tail": str(tail), "daemon_logs": "false"},
        timeout=30,
    )
    if not isinstance(data, dict) or not data.get("result_url"):
        return ""
    # Vast 把日志上传到 S3 有几秒延迟, result_url 立即取会 403 AccessDenied; 等就绪再返回
    url = data["result_url"]
    last_exc = None
    for _ in range(8):
        try:
            return request_text(url, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                last_exc = exc
                time.sleep(3)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return ""


def reconcile_vast_hashrate(config, state, rented, inst, contract_id, age):
    cfg = config.get("vast", {})
    if not cfg.get("hashrate_watch_enabled", True):
        return False
    if age < int(cfg.get("hashrate_grace_seconds", 300)):
        return False
    now_ts = epoch_now()
    last_check = float(rented.get("hashrate_last_check_epoch") or 0)
    interval = int(cfg.get("hashrate_watch_interval_seconds", 30))
    if now_ts - last_check < interval:
        return False
    rented["hashrate_last_check_epoch"] = now_ts
    hashrate_th = None
    try:
        log_text = request_vast_instance_logs(contract_id, int(cfg.get("hashrate_log_tail_lines", 300)))
        hashrate_th = parse_latest_hashrate(log_text)
    except Exception as exc:
        log(f"Vast hashrate log check failed: contract={contract_id} error={exc}; falling back to PearlHash worker API")
    if hashrate_th is None:
        worker = make_worker(config, "vast", rented.get("gpu"), rented.get("external_id"))
        try:
            info = pearl_worker_hashrates(config).get(worker)
        except Exception as exc:
            log(f"Vast PearlHash worker check failed: contract={contract_id} worker={worker} error={type(exc).__name__}: {exc}")
            info = None
        if info:
            hashrate_th = float(info.get("hashrate_th") or 0)
            rented["last_hashrate_lookup"] = {"worker": worker, "found": True, "ip": info.get("ip"), "version": info.get("version")}
        else:
            rented["last_hashrate_lookup"] = {"worker": worker, "found": False}
    if hashrate_th is None:
        return False
    price = float(rented.get("price") or inst.get("dph_total") or 0)
    if price <= 0:
        return False
    efficiency = hashrate_th / price
    min_eff = float(cfg.get("min_th_per_usd_hour", 250))
    min_hash = gpu_map_value(inst.get("gpu_name") or rented.get("gpu"), cfg.get("min_hashrate_th", {}))
    low = efficiency < min_eff or (min_hash is not None and hashrate_th < float(min_hash))
    rented["last_hashrate_th"] = round(hashrate_th, 3)
    rented["last_hashrate_efficiency"] = round(efficiency, 3)
    if not low:
        rented.pop("low_efficiency_since_epoch", None)
        rented.pop("low_efficiency_reason", None)
        return False
    if not rented.get("low_efficiency_since_epoch"):
        rented["low_efficiency_since_epoch"] = now_ts
        rented["low_efficiency_reason"] = f"hashrate={hashrate_th:.2f}TH efficiency={efficiency:.1f}TH_per_usd_hour"
        log(f"Vast low efficiency observed: contract={contract_id} gpu={inst.get('gpu_name') or rented.get('gpu')} price=${price:.3f}/h hashrate={hashrate_th:.2f}TH efficiency={efficiency:.1f}")
        return False
    duration = now_ts - float(rented["low_efficiency_since_epoch"])
    required = int(cfg.get("low_efficiency_stop_seconds", 900))
    if duration < required:
        return False
    reason = f"low_efficiency:{hashrate_th:.2f}TH:{efficiency:.1f}TH_per_usd_hour:{int(duration)}s"
    rented["active"] = False
    rented["inactive_reason"] = reason
    machine_ids = offer_machine_ids(inst)
    rented["machine_id"] = machine_ids[0] if machine_ids else inst.get("machine_id")
    rented["last_state"] = {
        "hashrate_th": round(hashrate_th, 3),
        "efficiency_th_per_usd_hour": round(efficiency, 3),
        "price": price,
        "duration_seconds": int(duration),
    }
    blacklist_offer(state, "vast", rented.get("external_id"), reason, {
        "contract_id": contract_id,
        "gpu": rented.get("gpu"),
        "price": price,
        "hashrate_th": hashrate_th,
    })
    for machine_id in machine_ids:
        blacklist_machine(state, "vast", machine_id, reason, {
            "contract_id": contract_id,
            "gpu": inst.get("gpu_name") or rented.get("gpu"),
            "location": inst.get("geolocation"),
        })
    try:
        result = destroy_vast_instance(contract_id)
        log(f"Vast low-efficiency destroyed: contract={contract_id} machine={inst.get('machine_id')} gpu={inst.get('gpu_name')} reason={reason} result={result}")
        notify(
            config,
            "Vast GPU stopped",
            f"{inst.get('gpu_name') or rented.get('gpu')} contract={contract_id} stopped for low efficiency: {reason}",
            priority="high",
            tags=["warning", "vast"],
        )
    except Exception as exc:
        log(f"Vast low-efficiency destroy failed: contract={contract_id} reason={reason} error={exc}")
    return True


def apply_low_efficiency_policy(config, state, provider, rented, hashrate_th, price, instance_id, stop_fn, details=None):
    cfg = config.get(provider, {})
    if hashrate_th is None or price <= 0:
        return False
    efficiency = hashrate_th / price
    min_eff = float(cfg.get("min_th_per_usd_hour", 0) or 0)
    min_hash = gpu_map_value(rented.get("gpu"), cfg.get("min_hashrate_th", {}))
    low = (min_eff > 0 and efficiency < min_eff) or (min_hash is not None and hashrate_th < float(min_hash))
    rented["last_hashrate_th"] = round(hashrate_th, 3)
    rented["last_hashrate_efficiency"] = round(efficiency, 3)
    if not low:
        rented.pop("low_efficiency_since_epoch", None)
        rented.pop("low_efficiency_reason", None)
        return False
    now_ts = epoch_now()
    if not rented.get("low_efficiency_since_epoch"):
        first_low_epoch = now_ts
        if provider == "runpod" and cfg.get("backdate_low_efficiency_for_existing", True):
            first_low_epoch = float(rented.get("created_epoch") or now_ts)
        rented["low_efficiency_since_epoch"] = first_low_epoch
        rented["low_efficiency_reason"] = f"hashrate={hashrate_th:.2f}TH efficiency={efficiency:.1f}TH_per_usd_hour"
        log(f"{provider} low efficiency observed: instance={instance_id} gpu={rented.get('gpu')} price=${price:.3f}/h hashrate={hashrate_th:.2f}TH efficiency={efficiency:.1f}")
        if provider != "runpod":
            return False
    duration = now_ts - float(rented["low_efficiency_since_epoch"])
    required = int(cfg.get("low_efficiency_stop_seconds", 900))
    if duration < required:
        return False
    reason = f"low_efficiency:{hashrate_th:.2f}TH:{efficiency:.1f}TH_per_usd_hour:{int(duration)}s"
    rented["active"] = False
    rented["inactive_reason"] = reason
    rented["last_state"] = {
        "hashrate_th": round(hashrate_th, 3),
        "efficiency_th_per_usd_hour": round(efficiency, 3),
        "price": price,
        "duration_seconds": int(duration),
        **(details or {}),
    }
    blacklist_offer(state, provider, rented.get("external_id"), reason, {
        "contract_id": instance_id,
        "gpu": rented.get("gpu"),
        "price": price,
        "hashrate_th": hashrate_th,
    })
    try:
        result = stop_fn(instance_id)
        log(f"{provider} low-efficiency stopped: instance={instance_id} gpu={rented.get('gpu')} reason={reason} result={result}")
        notify(
            config,
            f"{provider} GPU stopped",
            f"{rented.get('gpu')} instance={instance_id} stopped for low efficiency: {reason}",
            priority="high",
            tags=["warning", provider],
        )
    except Exception as exc:
        log(f"{provider} low-efficiency stop failed: instance={instance_id} reason={reason} error={exc}")
    return True


def reconcile_vast_instances(config, state):
    try:
        instances = list_vast_instances()
    except Exception as exc:
        log(f"Vast reconcile failed: {exc}")
        return
    by_contract = {str(x.get("id")): x for x in instances if x.get("id") is not None}
    by_offer = {}
    for inst in instances:
        label = str(inst.get("label") or "")
        parts = label.split("-")
        if parts and parts[-1].isdigit():
            by_offer[parts[-1]] = inst
    bad_states = {"stopped", "exited", "error", "failed", "unavailable"}
    pending_states = {"creating", "loading", "starting", "created"}
    creating_timeout = int(config.get("vast", {}).get("creating_timeout_seconds", 600))
    bad_status_patterns = (
        "error response from daemon",
        "failed to create task",
        "oci runtime create failed",
        "failed to inject cdi devices",
        "failed to start containers",
        "unknown flag: --runtime",
    )
    for rented in state.get("rented", []):
        if rented.get("provider") != "vast" or not rented.get("active", True):
            continue
        contract_id = str(rented.get("contract_id") or "")
        inst = by_contract.get(contract_id) if contract_id else by_offer.get(str(rented.get("external_id") or ""))
        if not inst:
            rented["active"] = False
            rented["inactive_reason"] = "missing_from_vast_instances"
            continue
        if not contract_id and inst.get("id") is not None:
            rented["contract_id"] = str(inst.get("id"))
            contract_id = rented["contract_id"]
        cur_state = str(inst.get("cur_state") or inst.get("actual_status") or "").lower()
        intended = str(inst.get("intended_status") or "").lower()
        actual = str(inst.get("actual_status") or "").lower()
        status_msg = str(inst.get("status_msg") or inst.get("status_message") or "").lower()
        startup_error = any(pattern in status_msg for pattern in bad_status_patterns)
        created_epoch = rented.get("created_epoch")
        if not created_epoch and rented.get("time"):
            try:
                created_epoch = dt.datetime.fromisoformat(str(rented["time"])).timestamp()
            except Exception:
                created_epoch = epoch_now()
        if inst.get("start_date") and float(inst.get("start_date") or 0) > 0:
            created_epoch = min(float(created_epoch or epoch_now()), float(inst["start_date"]))
        age = epoch_now() - float(created_epoch or epoch_now())
        timed_out = age >= creating_timeout and (cur_state in pending_states or actual in pending_states) and not status_msg
        fractional_gpu = inst.get("gpu_frac") is not None and float(inst.get("gpu_frac") or 0) < float(config.get("vast", {}).get("min_gpu_frac", 1.0))
        if cur_state in bad_states or intended in bad_states or startup_error or timed_out or fractional_gpu:
            reason = f"bad_state:{cur_state}/{intended}"
            if startup_error:
                reason = "startup_error"
            if timed_out:
                reason = f"creating_timeout:{int(age)}s"
            if fractional_gpu:
                reason = f"fractional_gpu:{inst.get('gpu_frac')}"
            rented["active"] = False
            rented["inactive_reason"] = reason
            machine_ids = offer_machine_ids(inst)
            rented["machine_id"] = machine_ids[0] if machine_ids else inst.get("machine_id")
            rented["last_state"] = {
                "cur_state": cur_state,
                "intended_status": intended,
                "actual_status": actual,
                "age_seconds": int(age),
                "gpu_frac": inst.get("gpu_frac"),
                "status_msg": status_msg[:300],
            }
            blacklist_offer(state, "vast", rented.get("external_id"), reason, {
                "contract_id": contract_id,
                "gpu": rented.get("gpu"),
                "price": rented.get("price"),
            })
            for machine_id in machine_ids:
                blacklist_machine(state, "vast", machine_id, reason, {
                    "contract_id": contract_id,
                    "gpu": inst.get("gpu_name") or rented.get("gpu"),
                    "location": inst.get("geolocation"),
                })
            try:
                result = destroy_vast_instance(contract_id)
                log(f"Vast unusable destroyed: contract={contract_id} machine={inst.get('machine_id')} gpu={inst.get('gpu_name')} state={cur_state}/{intended} reason={reason} result={result}")
                notify(
                    config,
                    "Vast GPU destroyed",
                    f"{inst.get('gpu_name') or rented.get('gpu')} contract={contract_id} destroyed: {reason}",
                    priority="default",
                    tags=["wastebasket", "vast"],
                )
            except Exception as exc:
                log(f"Vast destroy failed: contract={contract_id} state={cur_state}/{intended} error={exc}")
            continue
        reconcile_vast_hashrate(config, state, rented, inst, contract_id, age)


def runpod_existing_count(config):
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return 0, 0.0
    try:
        pods = list_runpod_pods()
    except Exception as exc:
        log(f"RunPod list failed: {exc}")
        return 0, 0.0
    if not isinstance(pods, list):
        return 0, 0.0
    running = [p for p in pods if str(p.get("desiredStatus") or p.get("status") or "").upper() not in ("EXITED", "TERMINATED", "STOPPED")]
    hourly = 0.0
    for pod in running:
        try:
            hourly += float(pod.get("costPerHr") or pod.get("adjustedCostPerHr") or 0)
        except (TypeError, ValueError):
            pass
    return len(running), hourly


def list_runpod_pods():
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return []
    pods = request_json("GET", "https://rest.runpod.io/v1/pods", {"Authorization": f"Bearer {api_key}"}, timeout=30)
    return pods if isinstance(pods, list) else []


def delete_runpod_pod(pod_id):
    api_key = os.environ["RUNPOD_API_KEY"]
    return request_json("DELETE", f"https://rest.runpod.io/v1/pods/{pod_id}", {"Authorization": f"Bearer {api_key}"}, timeout=30)


def request_runpod_pod_logs(pod_id, tail=120):
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key or not pod_id:
        return ""
    headers = {"Authorization": f"Bearer {api_key}"}
    candidates = [
        f"https://rest.runpod.io/v1/pods/{urllib.parse.quote(str(pod_id))}/logs?tail={int(tail)}",
        f"https://rest.runpod.io/v1/pods/{urllib.parse.quote(str(pod_id))}/logs",
    ]
    last_error = None
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return str(parsed.get("logs") or parsed.get("log") or parsed.get("data") or parsed)
            except Exception:
                pass
            return raw
        except Exception as exc:
            last_error = exc
    log(f"RunPod log fetch failed: pod={pod_id} error={last_error}")
    return ""


def reconcile_runpod_instances(config, state):
    if not config.get("runpod", {}).get("create_enabled", False):
        return
    cfg = config.get("runpod", {})
    try:
        pods = list_runpod_pods()
    except Exception as exc:
        log(f"RunPod reconcile failed: {exc}")
        return
    by_id = {str(p.get("id")): p for p in pods if p.get("id")}
    terminal = {"EXITED", "TERMINATED", "STOPPED", "FAILED"}
    tracked_active_ids = {
        str(r.get("contract_id") or r.get("external_id") or "")
        for r in state.get("rented", [])
        if r.get("provider") == "runpod" and r.get("active", True)
    }
    if cfg.get("cleanup_untracked_terminal_pods", False):  # 默认 false: 不删账号里非本工具创建的 terminal pod
        for pod in pods:
            pod_id = str(pod.get("id") or "")
            if not pod_id or pod_id in tracked_active_ids:
                continue
            status = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
            if status not in terminal:
                continue
            try:
                log_tail = request_runpod_pod_logs(pod_id, int(cfg.get("terminal_log_tail_lines", 120)))
                if log_tail:
                    preview = " | ".join(line.strip() for line in log_tail.splitlines()[-8:] if line.strip())
                    if preview:
                        log(f"RunPod untracked terminal pod logs: pod={pod_id} status={status} tail={preview[:900]}")
                result = delete_runpod_pod(pod_id)
                log(f"RunPod untracked terminal pod deleted: pod={pod_id} status={status} result={result}")
            except Exception as exc:
                log(f"RunPod untracked terminal delete failed: pod={pod_id} status={status} error={exc}")
    worker_hashrates = None
    worker_api_failed = False
    now_ts = epoch_now()
    interval = int(cfg.get("hashrate_watch_interval_seconds", 30))
    grace = int(cfg.get("hashrate_grace_seconds", 300))
    for rented in state.get("rented", []):
        if rented.get("provider") != "runpod" or not rented.get("active", True):
            continue
        pod_id = str(rented.get("contract_id") or rented.get("external_id") or "")
        pod = by_id.get(pod_id)
        if not pod:
            rented["active"] = False
            rented["inactive_reason"] = "missing_from_runpod_pods"
            continue
        status = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        if status in terminal:
            rented["active"] = False
            rented["inactive_reason"] = f"pod_terminal:{status}"
            machine_id = str(pod.get("machineId") or (pod.get("machine") or {}).get("id") or (rented.get("result") or {}).get("machineId") or "")
            age = now_ts - float(rented.get("created_epoch") or now_ts)
            log_tail = request_runpod_pod_logs(pod_id, int(cfg.get("terminal_log_tail_lines", 120)))
            if log_tail:
                rented["terminal_log_tail"] = log_tail[-4000:]
                preview = " | ".join(line.strip() for line in log_tail.splitlines()[-8:] if line.strip())
                if preview:
                    log(f"RunPod terminal pod logs: pod={pod_id} status={status} tail={preview[:900]}")
            short_exit_seconds = int(cfg.get("short_exit_blacklist_seconds", 60))
            if machine_id and age <= short_exit_seconds:
                reason = f"short_terminal:{status}"
                rented["machine_id"] = machine_id
                blacklist_machine(state, "runpod", machine_id, reason, {
                    "pod_id": pod_id,
                    "gpu": rented.get("gpu"),
                    "price": rented.get("price"),
                    "age_seconds": round(age, 1),
                    "log_tail": (log_tail or "")[-1000:],
                })
                log(f"RunPod machine blacklisted after short terminal: machine={machine_id} pod={pod_id} gpu={rented.get('gpu')} age={age:.1f}s status={status}")
            try:
                result = delete_runpod_pod(pod_id)
                log(f"RunPod terminal pod deleted: pod={pod_id} status={status} result={result}")
                notify(config, "RunPod pod deleted", f"{rented.get('gpu')} pod={pod_id} status={status}", priority="default", tags=["wastebasket", "runpod"])
            except Exception as exc:
                log(f"RunPod delete failed: pod={pod_id} status={status} error={exc}")
            continue
        machine_id = str(pod.get("machineId") or (pod.get("machine") or {}).get("id") or (rented.get("result") or {}).get("machineId") or "")
        if machine_id:
            rented["machine_id"] = machine_id
        age = now_ts - float(rented.get("created_epoch") or now_ts)
        if age < grace:
            continue
        last_check = float(rented.get("hashrate_last_check_epoch") or 0)
        if now_ts - last_check < interval:
            continue
        rented["hashrate_last_check_epoch"] = now_ts
        if worker_hashrates is None:
            try:
                worker_hashrates = pearl_worker_hashrates(config)
                worker_api_failed = False
            except Exception as exc:
                log(f"RunPod PearlHash worker check failed: {type(exc).__name__}: {exc}")
                worker_hashrates = {}
                worker_api_failed = True
        worker = ((rented.get("result") or {}).get("env") or {}).get("PRL_WORKER") or (pod.get("env") or {}).get("PRL_WORKER") or rented.get("result", {}).get("name") or pod.get("name")
        info = worker_hashrates.get(str(worker))
        if not info:
            rented["last_hashrate_lookup"] = {"worker": worker, "found": False}
            # worker 不在矿池 = 没在挖。仅当本轮矿池查询成功时按 0 算力计, 交低效策略在持续低效 N 秒后回收;
            # 矿池 API 临时故障(worker_api_failed)则跳过, 避免误杀好机器。
            if worker_api_failed or not bool(cfg.get("missing_worker_as_zero", True)):
                continue
            hashrate_th = 0.0
        else:
            hashrate_th = float(info.get("hashrate_th") or 0)
            rented["last_hashrate_lookup"] = {"worker": worker, "found": True, "ip": info.get("ip"), "version": info.get("version")}
        stopped = apply_low_efficiency_policy(
            config,
            state,
            "runpod",
            rented,
            hashrate_th,
            float(rented.get("price") or pod.get("costPerHr") or pod.get("adjustedCostPerHr") or 0),
            pod_id,
            lambda instance_id: delete_runpod_pod(instance_id),
            {"worker": worker, "machine_id": machine_id},
        )
        if stopped and machine_id:
            blacklist_machine(state, "runpod", machine_id, rented.get("inactive_reason", "low_efficiency"), {
                "pod_id": pod_id,
                "gpu": rented.get("gpu"),
                "worker": worker,
                "hashrate_th": hashrate_th,
            })


def runpod_observe(config, state):
    cfg = config["runpod"]
    if not cfg.get("observe_enabled", False):
        return
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        log("RunPod observe skipped: RUNPOD_API_KEY is not set")
        return
    query = """
query GpuTypes($id: String!, $secureCloud: Boolean!) {
  gpuTypes(input: { id: $id }) {
    id
    displayName
    lowestPrice(input: { gpuCount: 1, secureCloud: $secureCloud }) {
      stockStatus
      uninterruptablePrice
      availableGpuCounts
    }
  }
}
"""
    now_ts = epoch_now()
    last_error = float(state.get("runpod_observe_last_error_epoch") or 0)
    hits = []
    checked = 0
    observed_prices = {}
    cloud_types = cfg.get("cloud_types") or [cfg.get("cloud_type", "COMMUNITY")]
    for gpu_type, cap in cfg.get("thresholds", {}).items():
        for cloud_type in cloud_types:
            secure_cloud = str(cloud_type).upper() == "SECURE"
            try:
                data = request_graphql("https://api.runpod.io/graphql", api_key, query, {"id": gpu_type, "secureCloud": secure_cloud}, timeout=15)
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="replace")
                if now_ts - last_error > int(cfg.get("observe_error_log_interval_seconds", 300)):
                    log(f"RunPod observe failed: HTTP {exc.code} {body_text[:200]}")
                    state["runpod_observe_last_error_epoch"] = now_ts
                return
            except Exception as exc:
                if now_ts - last_error > int(cfg.get("observe_error_log_interval_seconds", 300)):
                    log(f"RunPod observe failed: {type(exc).__name__}: {exc}")
                    state["runpod_observe_last_error_epoch"] = now_ts
                return
            gpu_types = ((data or {}).get("data") or {}).get("gpuTypes") or []
            for item in gpu_types:
                checked += 1
                lowest = item.get("lowestPrice") or {}
                status = str(lowest.get("stockStatus") or "").upper()
                price = lowest.get("uninterruptablePrice")
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                observe_key = f"{str(cloud_type).upper()}:{gpu_type}"
                observed_prices[observe_key] = {
                    "price": price,
                    "status": status,
                    "counts": lowest.get("availableGpuCounts"),
                    "time": now_ts,
                    "cloud_type": str(cloud_type).upper(),
                }
                if str(cloud_type).upper() == "COMMUNITY":
                    observed_prices[gpu_type] = observed_prices[observe_key]
                if status in ("AVAILABLE", "HIGH", "MEDIUM", "LOW") and price <= float(cap):
                    hits.append((str(cloud_type).upper(), gpu_type, price, status, lowest.get("availableGpuCounts")))
    state["runpod_observe_last_checked_epoch"] = now_ts
    state["runpod_observed_prices"] = observed_prices
    if hits:
        for cloud_type, gpu_type, price, status, counts in hits:
            log(f"RunPod observe hit: {cloud_type} {gpu_type} ${price:.3f}/h stock={status} counts={counts}")
    else:
        interval = int(cfg.get("observe_no_hit_log_interval_seconds", 300))
        last_no_hit = float(state.get("runpod_observe_last_no_hit_epoch") or 0)
        if now_ts - last_no_hit > interval:
            log(f"RunPod observe scanned: checked={checked} matches=0")
            state["runpod_observe_last_no_hit_epoch"] = now_ts


def try_runpod_create(config, state, live):
    cfg = config["runpod"]
    if renting_paused("runpod"):
        return
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        log("RunPod skipped: RUNPOD_API_KEY is not set")
        return
    count, hourly = runpod_existing_count(config)
    log(f"RunPod existing pods: count={count} hourly=${hourly:.3f}")
    runpod_observe(config, state)
    if not cfg.get("create_enabled", False):
        log("RunPod create disabled in config; not attempting Pod creation")
        return
    if not live:
        log("Dry run: RunPod create would be attempted only with --live")
        return
    reconcile_runpod_instances(config, state)
    if active_count_excluding(state, "runpod") + count >= int(config.get("max_active_instances", 1)):
        return
    if active_hourly_excluding(state, "runpod") + hourly >= float(config.get("max_total_hourly_usd", 0)):
        return
    cloud_types = cfg.get("cloud_types") or [cfg.get("cloud_type", "COMMUNITY")]
    unrestricted_keywords = [str(x) for x in cfg.get("unrestricted_country_gpu_keywords", [])]
    for gpu_type, cap in cfg.get("thresholds", {}).items():
        country_codes = cfg.get("country_codes", [])
        if any(keyword in gpu_type for keyword in unrestricted_keywords):
            country_codes = []
        for cloud_type in cloud_types:
            key = f"runpod:{cloud_type}:{gpu_type}"
            legacy_key = f"runpod:{gpu_type}"
            seen_entry = state.get("seen", {}).get(key) or state.get("seen", {}).get(legacy_key)
            if seen_entry:
                retry_seconds = int(cfg.get("create_retry_seconds", 20))
                try:
                    seen_epoch = dt.datetime.fromisoformat(str(seen_entry.get("time"))).timestamp()
                except Exception:
                    seen_epoch = 0
                if epoch_now() - seen_epoch < retry_seconds:
                    continue
            cloud_name = str(cloud_type).upper()
            actual_cooldowns = state.setdefault("runpod_actual_above_cap_cooldowns", {})
            cooldown_key = f"{cloud_name}:{gpu_type}"
            cooldown_until = float(actual_cooldowns.get(cooldown_key) or 0)
            if epoch_now() < cooldown_until:
                continue
            observed = (state.get("runpod_observed_prices") or {}).get(cooldown_key) or (state.get("runpod_observed_prices") or {}).get(gpu_type) or {}
            observed_price = observed.get("price")
            observed_status = str(observed.get("status") or "").upper()
            conservative_factor = float(cfg.get("create_observed_price_factor", 0.5))
            if cfg.get("require_observe_hit_for_create", True):
                if not observed or observed_status not in ("AVAILABLE", "HIGH", "MEDIUM", "LOW"):
                    log(f"RunPod conservative skip: {gpu_type} no fresh available observe hit")
                    mark_seen(state, "runpod", f"{cloud_type}:{gpu_type}", {"cap": cap, "cloud_type": cloud_type, "conservative_skip": True, "reason": "no_observe_hit"})
                    continue
            if observed_price is not None and float(observed_price) > float(cap) * conservative_factor:
                log(f"RunPod conservative skip: {gpu_type} observed=${float(observed_price):.3f}/h > {conservative_factor:.2f}*cap ${float(cap):.3f}/h")
                mark_seen(state, "runpod", f"{cloud_type}:{gpu_type}", {"cap": cap, "cloud_type": cloud_type, "observed_price": observed_price, "conservative_skip": True})
                continue
            old_price = cfg.get("_current_price")
            cfg["_current_price"] = cap
            env = make_env(config, "runpod", gpu_type, int(time.time()))
            if old_price is None:
                cfg.pop("_current_price", None)
            else:
                cfg["_current_price"] = old_price
            body = {
                "cloudType": cloud_type,
                "computeType": "GPU",
                "gpuCount": 1,
                "gpuTypeIds": [gpu_type],
                "gpuTypePriority": "custom",
                "imageName": config["image"],
                "env": env,
                "interruptible": False,
                "containerDiskInGb": int(cfg.get("container_disk_gb", 20)),
                "volumeInGb": int(cfg.get("volume_gb", 1)),
                "minVCPUPerGPU": int(cfg.get("min_vcpu_per_gpu", 2)),
                "minRAMPerGPU": int(cfg.get("min_ram_per_gpu", 8)),
                "minDownloadMbps": int(cfg.get("min_download_mbps", 50)),
                "minUploadMbps": int(cfg.get("min_upload_mbps", 20)),
                "countryCodes": country_codes,
                "name": env["PRL_WORKER"],
            }
            registry_auth_id = cfg.get("container_registry_auth_id") or os.environ.get("RUNPOD_CONTAINER_REGISTRY_AUTH_ID")
            if registry_auth_id:
                body["containerRegistryAuthId"] = registry_auth_id
            mark_seen(state, "runpod", f"{cloud_type}:{gpu_type}", {"cap": cap, "cloud_type": cloud_type, "country_codes": country_codes})
            try:
                result = request_json("POST", "https://rest.runpod.io/v1/pods", {"Authorization": f"Bearer {api_key}"}, body, timeout=60)
                cost = float(result.get("costPerHr") or result.get("adjustedCostPerHr") or 999)
                if cost > float(cap):
                    pod_id = result.get("id")
                    machine_id = str(result.get("machineId") or (result.get("machine") or {}).get("id") or "")
                    if machine_id:
                        blacklist_machine(state, "runpod", machine_id, "actual_price_above_cap", {
                            "pod_id": pod_id,
                            "gpu": gpu_type,
                            "actual_price": cost,
                            "cap": float(cap),
                            "observed_price": observed_price,
                            "cloud_type": cloud_type,
                        })
                    cooldown_seconds = int(cfg.get("actual_above_cap_cooldown_seconds", 90))
                    state.setdefault("runpod_actual_above_cap_cooldowns", {})[f"{cloud_type}:{gpu_type}"] = epoch_now() + cooldown_seconds
                    log(f"RunPod created above cap ${cost:.3f}/h > ${float(cap):.3f}/h; deleting pod={pod_id} machine={machine_id or 'unknown'} cooldown={cooldown_seconds}s")
                    if pod_id:
                        request_json("DELETE", f"https://rest.runpod.io/v1/pods/{pod_id}", {"Authorization": f"Bearer {api_key}"}, timeout=30)
                    continue
                machine_id = str(result.get("machineId") or (result.get("machine") or {}).get("id") or "")
                if machine_id and f"runpod:{machine_id}" in state.get("blacklist", {}).get("machines", {}):
                    pod_id = result.get("id")
                    log(f"RunPod created on blacklisted machine={machine_id}; deleting pod={pod_id}")
                    if pod_id:
                        request_json("DELETE", f"https://rest.runpod.io/v1/pods/{pod_id}", {"Authorization": f"Bearer {api_key}"}, timeout=30)
                    continue
                record_rent(state, "runpod", result.get("id"), gpu_type, cost, result)
                log(f"RunPod rent result: cloud={cloud_type} gpu={gpu_type} cost=${cost:.3f}/h id={result.get('id')}")
                notify(
                    config,
                    "RunPod GPU rented",
                    f"{cloud_type} {gpu_type} ${cost:.3f}/h pod={result.get('id')}",
                    priority="high",
                    tags=["white_check_mark", "runpod"],
                )
                return
            except urllib.error.HTTPError as exc:
                try:
                    body_text = exc.read().decode("utf-8")
                except Exception:
                    body_text = str(exc)
                log(f"RunPod create failed for {cloud_type} {gpu_type}: HTTP {exc.code} {body_text[:300]}")
            except Exception as exc:
                log(f"RunPod create failed for {cloud_type} {gpu_type}: {exc}")


def run_runpod_cycle(config, state, live):
    if config.get("runpod", {}).get("enabled", False):
        try_runpod_create(config, state, live)


def tensordock_headers():
    token = os.environ.get("TENSORDOCK_API_TOKEN")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def tensordock_url(config, path):
    return config["tensordock"].get("base_url", "https://dashboard.tensordock.com/api/v2").rstrip("/") + path


def read_text_file(path):
    if not path:
        return ""
    p = Path(path).expanduser()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def td_get_items(data):
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            attrs = nested.get("attributes")
            for key in ("items", "results", "hostnodes", "instances", "locations"):
                if isinstance(nested.get(key), list):
                    return nested[key]
                if isinstance(attrs, dict) and isinstance(attrs.get(key), list):
                    return attrs[key]
        for key in ("items", "results", "hostnodes", "instances", "locations"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


def td_location_value(node, key):
    loc = node.get("location") or {}
    if key == "city":
        return loc.get("city") or node.get("city") or ""
    if key == "state":
        return loc.get("stateprovince") or loc.get("state") or node.get("stateprovince") or node.get("state") or ""
    if key == "country":
        return loc.get("country") or node.get("country") or ""
    if key == "id":
        return loc.get("uuid") or loc.get("id") or node.get("location_id") or node.get("locationId") or node.get("uuid") or node.get("id")
    return ""


def td_gpu_price(gpu):
    for key in ("price_per_hr", "price", "pricePerHour", "hourlyPrice"):
        if gpu.get(key) is not None:
            return float(gpu[key])
    return None


def td_gpu_count(gpu):
    for key in ("availableCount", "available_count", "max_count", "count"):
        if gpu.get(key) is not None:
            try:
                return int(gpu[key])
            except (TypeError, ValueError):
                return 0
    return 0


def td_gpu_name(gpu):
    return gpu.get("v0Name") or gpu.get("displayName") or gpu.get("name") or gpu.get("model") or ""


def td_location_allowed(cfg, gpu_name, node):
    city = str(td_location_value(node, "city")).lower()
    state = str(td_location_value(node, "state")).lower()
    global_excluded_states = {str(x).lower() for x in cfg.get("excluded_states", [])}
    if state in global_excluded_states:
        return False
    rules = cfg.get("location_rules", {})
    for rule_gpu, rule in rules.items():
        if not gpu_matches(gpu_name, rule_gpu):
            continue
        excluded_states = {str(x).lower() for x in rule.get("excluded_states", [])}
        if state in excluded_states:
            return False
        rule_city = str(rule.get("city") or "").lower()
        rule_state = str(rule.get("state") or "").lower()
        if rule_city and city != rule_city:
            return False
        if rule_state and state != rule_state:
            return False
    return True


def td_offer_from_location(config, state, location, gpu):
    cfg = config.get("tensordock", {})
    name = td_gpu_name(gpu)
    cap = threshold_for(name, cfg.get("thresholds", {}))
    if cap is None:
        return None
    if not td_location_allowed(cfg, name, location):
        return None
    available = td_gpu_count(gpu)
    if available < 1:
        return None
    price = td_gpu_price(gpu)
    if price is None:
        price = cap
    if price > cap:
        return None
    storage = float(location.get("max_storage") or location.get("maxStorage") or location.get("storage_gb") or 0)
    ram = float(location.get("max_ram") or location.get("maxRam") or location.get("ram_gb") or 0)
    vcpu = float(location.get("max_vcpus") or location.get("maxVcpus") or location.get("vcpu_count") or 0)
    # Some API responses omit aggregate max resources. Treat missing as unknown, not as zero.
    if storage and storage < float(cfg.get("storage_gb", 100)):
        return None
    if ram and ram < float(cfg.get("ram_gb", 4)):
        return None
    if vcpu and vcpu < float(cfg.get("vcpu_count", 2)):
        return None
    location_id = td_location_value(location, "id")
    offer_id = f"{location_id}:{td_gpu_name(gpu)}"
    offer = {"id": offer_id, "machine_id": location_id}
    if is_blacklisted(state, "tensordock", offer):
        return None
    city = td_location_value(location, "city")
    state_name = td_location_value(location, "state")
    return {
        "provider": "tensordock",
        "id": offer_id,
        "hostnode_id": None,
        "location_id": location_id,
        "gpu": name,
        "price": float(price),
        "location": ", ".join([x for x in (city, state_name) if x]),
        "raw": {"location": location, "gpu": gpu},
    }


def tensordock_log_error(state, cfg, message):
    """标记本轮扫描出错，并节流错误日志：每 error_log_interval_seconds 才打一条。"""
    state["_td_scan_err"] = True
    interval = int(cfg.get("error_log_interval_seconds", 300))
    now_ts = epoch_now()
    last = float(state.get("tensordock_last_error_log_epoch") or 0)
    if now_ts - last >= interval:
        log(message)
        state["tensordock_last_error_log_epoch"] = now_ts


def tensordock_mark_ok(state):
    """任一请求成功即标记本轮扫描 OK（用于退避复位）。"""
    state["_td_scan_ok"] = True


def find_tensordock_location_offers(config, state, headers):
    try:
        data = request_json("GET", tensordock_url(config, "/locations"), headers, timeout=30)
    except Exception as exc:
        tensordock_log_error(state, config.get("tensordock", {}), f"TensorDock locations failed: {exc}")
        return []
    tensordock_mark_ok(state)
    locations = td_get_items(data)
    matches = []
    total_gpu_rows = 0
    for location in locations:
        gpus = location.get("gpus") or location.get("available_gpus") or location.get("availableGpus") or []
        if isinstance(gpus, dict):
            gpus = list(gpus.values())
        total_gpu_rows += len(gpus)
        for gpu in gpus:
            match = td_offer_from_location(config, state, location, gpu)
            if match:
                matches.append(match)
    log(f"TensorDock locations scanned: locations={len(locations)} gpu_rows={total_gpu_rows} matches={len(matches)}")
    return matches


def find_tensordock_hostnode_offers(config, state, headers):
    cfg = config.get("tensordock", {})
    matches = []
    scanned_nodes = 0
    scanned_gpu_rows = 0
    gpu_slugs = cfg.get("gpu_slugs", {
        "RTX 3090": "geforcertx3090-pcie-24gb",
        "RTX 4090": "geforcertx4090-pcie-24gb",
        "RTX 5090": "geforcertx5090-pcie-32gb",
    })
    for wanted_gpu in cfg.get("thresholds", {}).keys():
        slug = gpu_slugs.get(wanted_gpu, wanted_gpu)
        query = (
            f"/hostnodes?gpu={urllib.parse.quote(str(slug))}"
            f"&minRamGb={int(cfg.get('ram_gb', 4))}"
            f"&minVcpu={int(cfg.get('vcpu_count', 2))}"
            f"&minStorageGb={int(cfg.get('storage_gb', 100))}"
        )
        try:
            data = request_json("GET", tensordock_url(config, query), headers, timeout=25)
        except Exception as exc:
            tensordock_log_error(state, cfg, f"TensorDock hostnodes failed for {wanted_gpu}: {exc}")
            continue
        tensordock_mark_ok(state)
        nodes = td_get_items(data)
        scanned_nodes += len(nodes)
        for node in nodes:
            resources = node.get("available_resources") or node.get("availableResources") or {}
            if float(resources.get("vcpu_count") or resources.get("vcpus") or 0) < float(cfg.get("vcpu_count", 2)):
                continue
            if float(resources.get("ram_gb") or resources.get("ramGb") or 0) < float(cfg.get("ram_gb", 4)):
                continue
            if float(resources.get("storage_gb") or resources.get("storageGb") or 0) < float(cfg.get("storage_gb", 100)):
                continue
            gpus = resources.get("gpus") or node.get("gpus") or []
            if isinstance(gpus, dict):
                gpus = list(gpus.values())
            scanned_gpu_rows += len(gpus)
            for gpu in gpus:
                name = td_gpu_name(gpu)
                cap = threshold_for(name, cfg.get("thresholds", {}))
                if cap is None:
                    continue
                if not td_location_allowed(cfg, name, node):
                    continue
                available = td_gpu_count(gpu)
                if available < 1:
                    continue
                price = td_gpu_price(gpu)
                if price is None:
                    price = cap
                if price > cap:
                    continue
                hostnode_id = node.get("uuid") or node.get("id") or node.get("hostnode_id")
                offer_id = f"{hostnode_id}:{td_gpu_name(gpu)}"
                offer = {"id": offer_id, "machine_id": hostnode_id}
                if is_blacklisted(state, "tensordock", offer):
                    continue
                matches.append({
                    "provider": "tensordock",
                    "id": offer_id,
                    "hostnode_id": hostnode_id,
                    "location_id": td_location_value(node, "id"),
                    "gpu": name,
                    "price": float(price),
                    "location": ", ".join([x for x in (td_location_value(node, "city"), td_location_value(node, "state")) if x]),
                    "raw": {"node": node, "gpu": gpu},
                })
    if state.get("_td_scan_ok") or matches:
        log(f"TensorDock hostnodes scanned: nodes={scanned_nodes} gpu_rows={scanned_gpu_rows} matches={len(matches)}")
    return matches


def find_tensordock_offers(config, state):
    headers = tensordock_headers()
    if not headers:
        log("TensorDock skipped: TENSORDOCK_API_TOKEN is not set")
        return []
    cfg = config.get("tensordock", {})
    now_ts = epoch_now()
    # 退避闸：上一轮判定 API 故障后，在退避截止前直接跳过，不再打 API、不刷屏。
    if now_ts < float(state.get("tensordock_backoff_until_epoch") or 0):
        return []
    state["_td_scan_ok"] = False
    state["_td_scan_err"] = False
    matches = []
    if cfg.get("prefer_hostnodes", True) and cfg.get("scan_hostnodes", True):
        matches.extend(find_tensordock_hostnode_offers(config, state, headers))
    if not matches and cfg.get("scan_locations", True):
        matches.extend(find_tensordock_location_offers(config, state, headers))
    if not matches and not cfg.get("prefer_hostnodes", True) and cfg.get("scan_hostnodes", True):
        matches.extend(find_tensordock_hostnode_offers(config, state, headers))
    # 本轮所有请求都失败（无一成功）→ 视为 API 故障，指数退避降低重试频率。
    if state.get("_td_scan_err") and not state.get("_td_scan_ok"):
        streak = int(state.get("tensordock_fail_streak", 0)) + 1
        state["tensordock_fail_streak"] = streak
        base = int(cfg.get("scan_backoff_base_seconds", 30))
        cap = int(cfg.get("scan_backoff_max_seconds", 600))
        delay = min(base * (2 ** min(streak - 1, 20)), cap)
        state["tensordock_backoff_until_epoch"] = now_ts + delay
        interval = int(cfg.get("error_log_interval_seconds", 300))
        last_bo = float(state.get("tensordock_last_backoff_log_epoch") or 0)
        if now_ts - last_bo >= interval:
            log(f"TensorDock API failing ({streak} consecutive); backing off {delay}s before next scan")
            state["tensordock_last_backoff_log_epoch"] = now_ts
    elif state.get("_td_scan_ok"):
        if int(state.get("tensordock_fail_streak", 0)) > 0:
            log("TensorDock API recovered; resuming normal scan cadence")
        state["tensordock_fail_streak"] = 0
        state["tensordock_backoff_until_epoch"] = 0
    seen_ids = set()
    unique = []
    for match in sorted(matches, key=lambda x: x["price"]):
        if match["id"] in seen_ids:
            continue
        seen_ids.add(match["id"])
        unique.append(match)
    return unique


def tensordock_cloud_init(config, match):
    worker = make_worker(config, "td", match["gpu"], str(match["id"]).split(":")[0])
    prl_address = config["prl_address"]
    prl_host = config["prl_host"]
    cfg = config.get("tensordock", {})
    driver_packages = gpu_map_value(match["gpu"], cfg.get("nvidia_driver_packages_by_gpu", {}))
    if not driver_packages:
        driver_packages = cfg.get("nvidia_driver_packages") or [
        "nvidia-driver-580-open nvidia-utils-580",
        "nvidia-driver-595-open nvidia-utils-595",
        ]
    driver_install = " || ".join(
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y {packages}"
        for packages in driver_packages
    )
    gpu_power_limits = cfg.get("power_limit_watts", {})
    power_limit = gpu_power_limits.get(match["gpu"]) if isinstance(gpu_power_limits, dict) else None
    power_limit_line = f"nvidia-smi -pl {int(power_limit)} || true" if power_limit else "true"
    watchdog_interval = int(cfg.get("hashrate_watch_interval_seconds", 30))
    watchdog_zero_seconds = int(cfg.get("hashrate_zero_recover_seconds", 120))
    alert_url = str(config.get("alert_url") or "").strip()
    alert_shell = f"""alert_url={json.dumps(alert_url)}
notify() {{
  [ -n "$alert_url" ] || return 0
  title="$1"
  shift
  curl -fsS -m 10 -H "Title: $title" -H "Priority: high" -H "Tags: bell" -d "$*" "$alert_url" >/dev/null 2>&1 || true
}}
"""
    return {
        "write_files": [
            {
                "path": "/usr/local/bin/setup-pearl-miner.sh",
                "permissions": "0755",
                "content": f"""#!/usr/bin/env bash
set -euo pipefail
exec >>/var/log/pearl-miner-setup.log 2>&1
{alert_shell}

echo "=== setup start $(date -Is) ==="
apt-get update -y
apt-get install -y curl pciutils ubuntu-drivers-common nvidia-modprobe

if ! command -v nvidia-smi >/dev/null 2>&1; then
  {driver_install}
fi

if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/tmp/nvidia-smi.out 2>&1; then
  if [ ! -f /var/lib/pearl-miner-driver-rebooted ]; then
    touch /var/lib/pearl-miner-driver-rebooted
    echo "nvidia-smi unavailable after driver install; rebooting once"
    systemctl reboot
    exit 0
  fi
  cat /tmp/nvidia-smi.out || true
  echo "nvidia-smi still unavailable after reboot marker; leaving setup failed"
  exit 1
fi

nvidia-modprobe -u -c=0 || true
nvidia-smi -pm 1 || true
{power_limit_line}

mkdir -p /opt/pearl-miner
if [ ! -x /opt/pearl-miner/pearl-miner ]; then
  curl -L -o /opt/pearl-miner/pearl-miner https://pearlhash.xyz/downloads/pearl-miner-v10
  chmod +x /opt/pearl-miner/pearl-miner
fi

cat >/etc/systemd/system/pearl-miner.service <<'EOF'
[Unit]
Description=PearlHash Miner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/pearl-miner
ExecStart=/opt/pearl-miner/pearl-miner --host {prl_host} --user {prl_address} --worker {worker}
Restart=always
RestartSec=5
StandardOutput=append:/var/log/pearl-miner.log
StandardError=append:/var/log/pearl-miner.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pearl-miner.service
systemctl restart pearl-miner.service
systemctl enable --now pearl-miner-watchdog.service
for i in $(seq 1 30); do
  sleep 10
  hash="$(awk '/Hashrate Total/ {{ for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+(\\.[0-9]+)?$/) val=$i+0 }} END {{ if (val != "") print val; else print 0 }}' /var/log/pearl-miner.log 2>/dev/null || echo 0)"
  if awk "BEGIN {{exit !($hash > 0)}}"; then
    notify "TensorDock miner running" "{match['gpu']} worker {worker} hashrate ${hash} TH/s"
    break
  fi
done
systemctl disable pearl-miner-setup.service || true
echo "=== setup complete $(date -Is) ==="
""",
            },
            {
                "path": "/etc/systemd/system/pearl-miner-setup.service",
                "permissions": "0644",
                "content": """[Unit]
Description=Install NVIDIA driver and start PearlHash miner
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/setup-pearl-miner.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
""",
            },
            {
                "path": "/usr/local/bin/pearl-miner-watchdog.sh",
                "permissions": "0755",
                "content": f"""#!/usr/bin/env bash
set -euo pipefail
{alert_shell}

log=/var/log/pearl-miner.log
watch_log=/var/log/pearl-miner-watchdog.log
interval={watchdog_interval}
zero_limit={watchdog_zero_seconds}
had_hash=0
zero_since=0

extract_hash() {{
  [ -f "$log" ] || return 1
  awk '
    /Hashrate Total/ {{
      for (i = 1; i <= NF; i++) {{
        if ($i ~ /^[0-9]+(\\.[0-9]+)?$/) {{
          val = $i + 0
        }}
      }}
    }}
    END {{
      if (val == "") exit 1
      print val
    }}
  ' "$log"
}}

while true; do
  hash="$(extract_hash || echo 0)"
  if awk "BEGIN {{exit !($hash > 0)}}"; then
    had_hash=1
    zero_since=0
  elif [ "$had_hash" = "1" ]; then
    if [ "$zero_since" = "0" ]; then
      zero_since="$(date +%s)"
    fi
    now="$(date +%s)"
    if [ $((now - zero_since)) -ge "$zero_limit" ]; then
      echo "$(date -Is) hashrate dropped to zero for $((now - zero_since))s; restarting miner" >> "$watch_log"
      notify "TensorDock zero hashrate" "{match['gpu']} worker {worker} dropped to zero for $((now - zero_since))s; restarting"
      systemctl stop pearl-miner.service || true
      nvidia-smi -r >> "$watch_log" 2>&1 || true
      nvidia-modprobe -u -c=0 >> "$watch_log" 2>&1 || true
      nvidia-smi -pm 1 >> "$watch_log" 2>&1 || true
      {power_limit_line} >> "$watch_log" 2>&1 || true
      systemctl restart pearl-miner.service || true
      zero_since=0
      sleep 20
    fi
  fi
  sleep "$interval"
done
""",
            },
            {
                "path": "/etc/systemd/system/pearl-miner-watchdog.service",
                "permissions": "0644",
                "content": """[Unit]
Description=PearlHash hashrate watchdog
After=pearl-miner.service

[Service]
Type=simple
ExecStart=/usr/local/bin/pearl-miner-watchdog.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
""",
            },
        ],
        "runcmd": [
            "systemctl daemon-reload",
            "systemctl enable --now pearl-miner-setup.service",
        ]
    }


def rent_tensordock(config, match, state, live):
    cfg = config["tensordock"]
    if already_seen(state, "tensordock", match["id"]):
        return False
    if active_count(state) >= int(config.get("max_active_instances", 1)):
        log(f"TensorDock hit but max_active_instances reached: {match['gpu']} ${match['price']:.3f}/h {match['location']}")
        return False
    if active_hourly(state) + float(match["price"]) > float(config.get("max_total_hourly_usd", 0)):
        log(f"TensorDock hit but max_total_hourly_usd reached: {match['gpu']} ${match['price']:.3f}/h {match['location']}")
        return False
    log(f"TensorDock hit: {match['gpu']} ${match['price']:.3f}/h {match['location']} offer={match['id']}")
    if not live:
        log("Dry run: not renting TensorDock offer")
        return False
    headers = tensordock_headers()
    if not headers:
        return False
    ssh_key = read_text_file(cfg.get("ssh_key_path"))
    if not ssh_key:
        log(f"TensorDock skipped: SSH public key missing at {cfg.get('ssh_key_path')}")
        return False
    mark_seen(state, "tensordock", match["id"], {"gpu": match["gpu"], "price": match["price"]})
    gpu_slug = td_gpu_name(match.get("raw", {}).get("gpu", {})) or match["gpu"]
    attributes = {
        "type": "virtualmachine",
        "location_id": match.get("location_id") if not match.get("hostnode_id") else None,
        "hostnode_id": match.get("hostnode_id"),
        "image": cfg.get("image", "ubuntu2404"),
        "resources": {
            "vcpu_count": int(cfg.get("vcpu_count", 2)),
            "ram_gb": int(cfg.get("ram_gb", 4)),
            "storage_gb": int(cfg.get("storage_gb", 100)),
            "gpus": {gpu_slug: {"count": 1}},
        },
        "ssh_key": ssh_key,
        "name": make_worker(config, "td", match["gpu"], str(match["id"]).split(":")[0]),
        "cloud_init": tensordock_cloud_init(config, match),
        "useDedicatedIp": bool(cfg.get("use_dedicated_ip", True)),
    }
    attributes = {k: v for k, v in attributes.items() if v not in (None, "")}
    body = {
        "data": {
            "type": "virtualmachine",
            "attributes": attributes,
        }
    }
    try:
        result = request_json("POST", tensordock_url(config, "/instances"), headers, body, timeout=60)
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            body_text = str(exc)
        log(f"TensorDock rent failed: offer={match['id']} HTTP {exc.code} {body_text[:500]}")
        lower_text = body_text.lower()
        if exc.code in (400, 404, 409, 410) or "not available" in lower_text or "insufficient" in lower_text or "sold out" in lower_text:
            reason = "deployment_error" if exc.code == 400 else "offer_not_available"
            blacklist_offer(state, "tensordock", match["id"], reason, {"gpu": match["gpu"], "price": match["price"], "error": body_text[:300]})
        return False
    except Exception as exc:
        log(f"TensorDock rent failed: offer={match['id']} {exc}")
        return False
    if isinstance(result, dict) and int(result.get("status") or 0) >= 400:
        log(f"TensorDock rent failed: offer={match['id']} API status={result.get('status')} error={result.get('error')}")
        blacklist_offer(state, "tensordock", match["id"], "deployment_error", {"gpu": match["gpu"], "price": match["price"], "error": result.get("error")})
        return False
    record_rent(state, "tensordock", match["id"], match["gpu"], match["price"], result or {})
    log(f"TensorDock rent result: offer={match['id']} result={result}")
    notify(
        config,
        "TensorDock GPU rented",
        f"{match['gpu']} ${float(match['price']):.3f}/h {match['location']} offer={match['id']}",
        priority="high",
        tags=["white_check_mark", "tensordock"],
    )
    return True


def list_tensordock_instances(config):
    headers = tensordock_headers()
    if not headers:
        return []
    try:
        data = request_json("GET", tensordock_url(config, "/instances"), headers, timeout=30)
    except Exception as exc:
        log(f"TensorDock list failed: {exc}")
        return []
    instances = td_get_items(data)
    detailed = []
    for inst in instances:
        inst_id = str(inst.get("id") or inst.get("uuid") or inst.get("instance_id") or inst.get("instanceId") or "")
        if inst_id and not (inst.get("ipAddress") or inst.get("ip_address") or inst.get("public_ip")):
            try:
                detail = request_json("GET", tensordock_url(config, f"/instances/{urllib.parse.quote(inst_id)}"), headers, timeout=15)
                if isinstance(detail, dict):
                    inst = detail
            except Exception as exc:
                log(f"TensorDock detail failed: instance={inst_id} {exc}")
        detailed.append(inst)
    return detailed


def delete_tensordock_instance(config, instance_id):
    headers = tensordock_headers()
    if not headers:
        return None
    return request_json("DELETE", tensordock_url(config, f"/instances/{instance_id}"), headers, timeout=30)


def tensordock_ssh_log(config, host, lines=300):
    cfg = config.get("tensordock", {})
    user = cfg.get("ssh_user", "user")
    pub_path = str(cfg.get("ssh_key_path") or "")
    key_path = str(cfg.get("ssh_private_key_path") or (pub_path[:-4] if pub_path.endswith(".pub") else pub_path))
    if not host or not key_path:
        return ""
    cmd = [
        "ssh",
        "-i",
        key_path,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        f"{user}@{host}",
        f"tail -n {int(lines)} /var/log/pearl-miner.log 2>/dev/null || true",
    ]
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        return None  # SSH 取日志失败, 与"日志为空"区分(None=取不到, ""/文本=取到了)


def instance_public_ip(inst):
    for key in ("public_ip", "publicIp", "ip", "ip_address", "ipAddress"):
        if inst.get(key):
            return str(inst[key])
    network = inst.get("network") or {}
    for key in ("public_ip", "publicIp", "ip"):
        if network.get(key):
            return str(network[key])
    return ""


def instance_ssh_port(inst):
    for key in ("ssh_port", "sshPort"):
        if inst.get(key):
            return int(inst[key])
    ports = inst.get("ports") or []
    if isinstance(ports, list):
        for port in ports:
            if str(port.get("internal_port") or port.get("private_port") or port.get("port")) == "22":
                return int(port.get("external_port") or port.get("public_port") or 22)
    return 22


def tcp_connectable(host, port, timeout=4):
    if not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def reconcile_tensordock_instances(config, state):
    cfg = config.get("tensordock", {})
    timeout_s = int(cfg.get("ssh_timeout_seconds", 600))
    instances = list_tensordock_instances(config)
    by_id = {str(x.get("id") or x.get("uuid") or x.get("instance_id") or x.get("instanceId")): x for x in instances}
    for rented in state.get("rented", []):
        if rented.get("provider") != "tensordock" or not rented.get("active", True):
            continue
        contract_id = str(rented.get("contract_id") or "")
        inst = by_id.get(contract_id)
        if not inst:
            rented["active"] = False
            rented["inactive_reason"] = "missing_from_tensordock_instances"
            continue
        created_epoch = rented.get("created_epoch") or epoch_now()
        age = epoch_now() - float(created_epoch)
        host = instance_public_ip(inst)
        port = instance_ssh_port(inst)
        if tcp_connectable(host, port):
            rented["ssh_ready"] = True
            rented["ssh_host"] = host
            rented["ssh_port"] = port
            if cfg.get("hashrate_watch_enabled", True) and age >= int(cfg.get("hashrate_grace_seconds", 300)):
                now_ts = epoch_now()
                last_check = float(rented.get("hashrate_last_check_epoch") or 0)
                if now_ts - last_check >= int(cfg.get("hashrate_watch_interval_seconds", 30)):
                    rented["hashrate_last_check_epoch"] = now_ts
                    log_text = tensordock_ssh_log(config, host, int(cfg.get("hashrate_log_tail_lines", 300)))
                    if log_text is not None:  # None=SSH取日志失败, 本轮跳过避免误杀
                        hashrate_th = parse_latest_hashrate(log_text)
                        if hashrate_th is None:
                            hashrate_th = 0.0  # SSH 可达且日志取到但无 Hashrate = 矿机没出算力 → 按 0 进低效回收
                        rented["last_hashrate_th"] = round(hashrate_th, 3)
                        apply_low_efficiency_policy(
                            config,
                            state,
                            "tensordock",
                            rented,
                            hashrate_th,
                            float(rented.get("price") or 0),
                            contract_id,
                            lambda instance_id: delete_tensordock_instance(config, instance_id),
                            {"ssh_host": host},
                        )
            continue
        if age < timeout_s:
            continue
        reason = f"ssh_timeout:{int(age)}s"
        rented["active"] = False
        rented["inactive_reason"] = reason
        rented["last_state"] = {
            "age_seconds": int(age),
            "ssh_host": host,
            "ssh_port": port,
            "status": inst.get("status") or inst.get("state"),
        }
        blacklist_offer(state, "tensordock", rented.get("external_id"), reason, {"gpu": rented.get("gpu"), "price": rented.get("price")})
        try:
            result = delete_tensordock_instance(config, contract_id)
            log(f"TensorDock unusable deleted: instance={contract_id} gpu={rented.get('gpu')} reason={reason} result={result}")
        except Exception as exc:
            log(f"TensorDock delete failed: instance={contract_id} reason={reason} error={exc}")


def try_tensordock_create(config, state, live):
    cfg = config.get("tensordock", {})
    if renting_paused("tensordock"):
        return
    if not cfg.get("enabled", False):
        return
    if not cfg.get("create_enabled", False):
        return
    if live:
        reconcile_tensordock_instances(config, state)
    for match in find_tensordock_offers(config, state):
        if rent_tensordock(config, match, state, live):
            break


def run_tensordock_cycle(config, state, live):
    try_tensordock_create(config, state, live)


def salad_headers():
    api_key = os.environ.get("SALAD_API_KEY")
    if not api_key:
        return None
    return {"Salad-Api-Key": api_key}


def salad_url(config, path):
    cfg = config.get("salad", {})
    base = cfg.get("base_url", "https://api.salad.com/api/public").rstrip("/")
    org = urllib.parse.quote(str(cfg.get("organization_name") or ""))
    project = urllib.parse.quote(str(cfg.get("project_name") or ""))
    return f"{base}/organizations/{org}/projects/{project}{path}"


def list_salad_container_groups(config):
    headers = salad_headers()
    if not headers:
        log("Salad skipped: SALAD_API_KEY is not set")
        return []
    data = request_json("GET", salad_url(config, "/containers"), headers, timeout=30)
    return (data or {}).get("items") or []


def list_salad_instances(config, group_name):
    headers = salad_headers()
    if not headers:
        return []
    name = urllib.parse.quote(str(group_name))
    data = request_json("GET", salad_url(config, f"/containers/{name}/instances"), headers, timeout=30)
    return (data or {}).get("instances") or []


def reallocate_salad_instance(config, group_name, instance_id):
    if renting_paused("salad"):
        return {"skipped": "rent_paused"}
    headers = salad_headers()
    if not headers:
        raise RuntimeError("SALAD_API_KEY is not set")
    group = urllib.parse.quote(str(group_name))
    inst = urllib.parse.quote(str(instance_id))
    return request_json("POST", salad_url(config, f"/containers/{group}/instances/{inst}/reallocate"), headers, timeout=30)


def iso_millis_utc(ts):
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time_epoch(value):
    if not value:
        return 0.0
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def salad_logs_url(config, path):
    cfg = config.get("salad", {})
    base = cfg.get("base_url", "https://api.salad.com/api/public").rstrip("/")
    org = urllib.parse.quote(str(cfg.get("organization_name") or ""))
    return f"{base}/organizations/{org}{path}"


def salad_query_instance_hashrates(config, group_name, lookback_seconds):
    headers = salad_headers()
    if not headers:
        return {}
    now_ts = epoch_now()
    query = (
        'resource.type = "container"'
        f' and resource.labels.project_name = "{config.get("salad", {}).get("project_name", "")}"'
        f' and resource.labels.container_group_name = "{group_name}"'
    )
    body = {
        "start_time": iso_millis_utc(now_ts - int(lookback_seconds)),
        "end_time": iso_millis_utc(now_ts),
        "page_size": max(1, min(100, int(config.get("salad", {}).get("log_page_size", 100)))),
        "sort_order": "desc",
        "query": query,
    }
    data = request_json("POST", salad_logs_url(config, "/log-entries"), headers, body, timeout=30)
    items = (data or {}).get("items") or []
    by_instance = {}
    for item in items:
        text = str(item.get("text_log") or item.get("log") or item.get("message") or "")
        hashrate = parse_latest_hashrate(text)
        if hashrate is None:
            continue
        labels = ((item.get("resource") or {}).get("labels") or {})
        instance_id = labels.get("instance_id") or labels.get("container_group_instance_id") or item.get("instance_id")
        if not instance_id:
            continue
        instance_id = str(instance_id)
        if instance_id in by_instance:
            continue
        by_instance[instance_id] = {
            "hashrate_th": float(hashrate),
            "time": item.get("time") or item.get("timestamp"),
            "machine_id": labels.get("machine_id"),
            "gpu_name": parse_log_gpu_name(text),
            "text": text,
        }
    return by_instance


def salad_group_gpu(group, cfg):
    classes = (((group.get("container") or {}).get("resources") or {}).get("gpu_classes") or [])
    by_class = cfg.get("gpu_class_names", {})
    for class_id in classes:
        gpu = by_class.get(str(class_id))
        if gpu:
            return gpu
    return ""


def salad_group_gpus(group, cfg):
    classes = (((group.get("container") or {}).get("resources") or {}).get("gpu_classes") or [])
    by_class = cfg.get("gpu_class_names", {})
    gpus = []
    for class_id in classes:
        gpu = by_class.get(str(class_id))
        if gpu and gpu not in gpus:
            gpus.append(gpu)
    return gpus


def object_contains_text(value, needle):
    needle = str(needle or "").lower()
    if not needle:
        return False
    if isinstance(value, dict):
        return any(object_contains_text(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(object_contains_text(v, needle) for v in value)
    return needle in str(value).lower()


def salad_min_hashrate_for_group(group, cfg):
    gpus = salad_group_gpus(group, cfg)
    if len(gpus) == 1:
        return gpu_map_value(gpus[0], cfg.get("min_hashrate_th", {}), cfg.get("default_min_hashrate_th")), gpus[0], False
    values = []
    for gpu in gpus:
        value = gpu_map_value(gpu, cfg.get("min_hashrate_th", {}), None)
        if value is not None:
            values.append(float(value))
    if values:
        return min(values), ",".join(gpus), True
    return cfg.get("default_min_hashrate_th"), ",".join(gpus), len(gpus) != 1


def salad_group_worker_name(group):
    env = (group.get("container") or {}).get("environment_variables") or {}
    for key in ("PRL_WORKER", "WORKER_NAME"):
        value = env.get(key)
        if value:
            return str(value)
    return str(group.get("name") or "")


def salad_group_running_count(group):
    counts = ((group.get("current_state") or {}).get("instance_status_counts") or {})
    return int(counts.get("running_count") or 0)


def run_salad_cycle(config, state, live):
    cfg = config.get("salad", {})
    if not cfg.get("enabled", False):
        return
    if not live and not cfg.get("observe_enabled", True):
        return
    now_ts = epoch_now()
    try:
        groups = list_salad_container_groups(config)
    except Exception as exc:
        log(f"Salad list groups failed: {type(exc).__name__}: {exc}")
        return
    include = set(str(x) for x in cfg.get("include_container_groups", []))
    exclude = set(str(x) for x in cfg.get("exclude_container_groups", []))
    groups_by_name = {}
    for group in groups:
        name = str(group.get("name") or "")
        if not name or name in exclude:
            continue
        if include and name not in include:
            continue
        groups_by_name[name] = group
    if not groups_by_name:
        return
    watch = state.setdefault("salad_watch", {})
    instance_watch = state.setdefault("salad_instance_watch", {})
    interval = int(cfg.get("hashrate_watch_interval_seconds", 30))
    low_seconds = int(cfg.get("low_efficiency_stop_seconds", 180))
    cooldown_seconds = int(cfg.get("reallocate_cooldown_seconds", 600))
    log_lookback = int(cfg.get("log_lookback_seconds", 180))
    use_worker_fallback = bool(cfg.get("use_worker_fallback", False))
    worker_hashrates = {}
    if use_worker_fallback:
        try:
            worker_hashrates = pearl_worker_hashrates(config)
        except Exception as exc:
            log(f"Salad PearlHash worker check failed: {type(exc).__name__}: {exc}")
    alphapool_workers = None
    alphapool_worker_api_failed = False
    for name, group in groups_by_name.items():
        worker_name = salad_group_worker_name(group)
        watch_key = f"{name}:{worker_name}"
        running_count = salad_group_running_count(group)
        if running_count <= 0:
            continue
        entry = watch.setdefault(watch_key, {})
        if now_ts - float(entry.get("last_check_epoch") or 0) < interval:
            continue
        entry["last_check_epoch"] = now_ts
        min_hash, gpu, mixed_group = salad_min_hashrate_for_group(group, cfg)
        if min_hash is None:
            continue
        is_alphapool_group = object_contains_text(group, "alphaminetech/pearl-miner")
        if is_alphapool_group and bool(cfg.get("alphapool_worker_api_enabled", True)):
            if alphapool_workers is None:
                try:
                    alphapool_workers = alphapool_worker_hashrates(config)
                except Exception as exc:
                    log(f"Salad AlphaPool worker check failed: {type(exc).__name__}: {exc}")
                    alphapool_workers = {}
                    alphapool_worker_api_failed = True
            if alphapool_worker_api_failed:
                entry["last_hashrate_source"] = "alphapool_worker_api_failed"
                entry["last_hashrate_error_epoch"] = now_ts
                continue
            info = alphapool_workers.get(worker_name)
            if not info and not bool(cfg.get("alphapool_missing_worker_as_zero", True)):
                continue
            hashrate_th = float((info or {}).get("hashrate_th") or 0)
            if info and not bool(info.get("online", True)):
                hashrate_th = 0.0
            min_hash = float(cfg.get("alphapool_min_hashrate_th", min_hash))
            gpu = cfg.get("alphapool_monitor_gpu_names", ["RTX 5070"])[0] if cfg.get("alphapool_monitor_gpu_names") else "RTX 5070"
            entry["last_hashrate_th"] = round(hashrate_th, 3)
            entry["last_hashrate_source"] = "alphapool_worker_api"
            entry["alphapool_worker"] = info or {"found": False}
            entry["gpu"] = gpu
            entry["worker"] = worker_name
            entry["group"] = name
            entry["running_count"] = running_count
            if hashrate_th >= min_hash:
                entry.pop("low_since_epoch", None)
                entry.pop("low_reason", None)
                continue
            reason = f"alphapool_worker_hashrate={hashrate_th:.2f}TH<{min_hash:.2f}TH worker={worker_name}"
            if not entry.get("low_since_epoch"):
                entry["low_since_epoch"] = now_ts
                entry["low_reason"] = reason
                log(f"Salad AlphaPool low worker hashrate observed: group={name} {reason}")
                continue
            duration = now_ts - float(entry["low_since_epoch"])
            if duration < low_seconds:
                continue
            last_reallocate = float(entry.get("last_reallocate_epoch") or 0)
            if now_ts - last_reallocate < cooldown_seconds:
                continue
            if not bool(cfg.get("alphapool_reallocate_enabled", True)):
                entry["last_reallocate_skipped_epoch"] = now_ts
                entry["last_reallocate_skipped_reason"] = "alphapool_reallocate_disabled"
                continue
            try:
                instances = list_salad_instances(config, name)
                instance = next((x for x in instances if x.get("started") or x.get("ready") or str(x.get("state") or "").lower() == "running"), None) or (instances[0] if instances else None)
                if not instance or not instance.get("id"):
                    log(f"Salad AlphaPool reallocate skipped: group={name} worker={worker_name} no instance id found")
                    continue
                instance_id = str(instance["id"])
                result = reallocate_salad_instance(config, name, instance_id)
                entry["last_reallocate_epoch"] = now_ts
                entry["last_reallocated_instance_id"] = instance_id
                entry.pop("low_since_epoch", None)
                log(f"Salad reallocated AlphaPool low-worker instance: group={name} worker={worker_name} instance={instance_id} {reason} result={result}")
                notify(
                    config,
                    "Salad AlphaPool instance reallocated",
                    f"{name} worker={worker_name} instance={instance_id} {reason}",
                    priority="high",
                    tags=["warning", "salad", "alphapool"],
                )
            except Exception as exc:
                log(f"Salad AlphaPool reallocate failed: group={name} worker={worker_name} {reason} error={type(exc).__name__}: {exc}")
            continue
        try:
            instances = list_salad_instances(config, name)
            log_rates = salad_query_instance_hashrates(config, name, max(log_lookback, low_seconds + interval + 30))
        except Exception as exc:
            log(f"Salad instance/log check failed: group={name} error={type(exc).__name__}: {exc}")
            continue
        running_instances = [x for x in instances if x.get("started") or x.get("ready") or str(x.get("state") or "").lower() == "running"]
        for instance in running_instances:
            instance_id = str(instance.get("id") or "")
            if not instance_id:
                continue
            rate = log_rates.get(instance_id)
            if not rate:
                continue
            hashrate_th = float(rate.get("hashrate_th") or 0)
            inst_key = f"{name}:{instance_id}"
            inst_entry = instance_watch.setdefault(inst_key, {})
            inst_entry["last_check_epoch"] = now_ts
            inst_entry["last_hashrate_th"] = round(hashrate_th, 3)
            inst_entry["group"] = name
            inst_entry["instance_id"] = instance_id
            inst_entry["machine_id"] = rate.get("machine_id") or instance.get("machine_id")
            log_gpu = rate.get("gpu_name") or ""
            image_name = str(((group.get("container") or {}).get("image") or ""))
            is_alphapool_group = "alphaminetech/pearl-miner" in image_name or object_contains_text(group, "alphaminetech/pearl-miner")
            alpha_monitor_gpus = set(str(x).upper() for x in cfg.get("alphapool_monitor_gpu_names", []))
            if is_alphapool_group and alpha_monitor_gpus:
                if log_gpu and log_gpu.upper() not in alpha_monitor_gpus:
                    inst_entry["last_hashrate_skipped"] = {"reason": "alphapool_gpu_filter", "log_gpu": log_gpu}
                    continue
                min_hash = float(cfg.get("alphapool_min_hashrate_th", cfg.get("min_hashrate_th", {}).get(log_gpu or "RTX 5070", min_hash)))
                gpu = log_gpu or "RTX 5070"
            inst_entry["gpu"] = log_gpu or gpu
            inst_entry["mixed_group"] = mixed_group
            if hashrate_th >= float(min_hash):
                inst_entry.pop("low_since_epoch", None)
                inst_entry.pop("low_reason", None)
                continue
            reason = f"instance_hashrate={hashrate_th:.2f}TH<{float(min_hash):.2f}TH gpu={gpu or 'unknown'}"
            if not inst_entry.get("low_since_epoch"):
                inst_entry["low_since_epoch"] = now_ts
                inst_entry["low_reason"] = reason
                log(f"Salad low instance hashrate observed: group={name} instance={instance_id} machine={inst_entry.get('machine_id')} {reason}")
                continue
            duration = now_ts - float(inst_entry["low_since_epoch"])
            if duration < low_seconds:
                continue
            last_reallocate = float(inst_entry.get("last_reallocate_epoch") or 0)
            if now_ts - last_reallocate < cooldown_seconds:
                continue
            try:
                result = reallocate_salad_instance(config, name, instance_id)
                inst_entry["last_reallocate_epoch"] = now_ts
                inst_entry.pop("low_since_epoch", None)
                log(f"Salad reallocated low-hashrate instance: group={name} instance={instance_id} machine={inst_entry.get('machine_id')} {reason} result={result}")
                notify(
                    config,
                    "Salad instance reallocated",
                    f"{name} instance={instance_id} {reason}",
                    priority="high",
                    tags=["warning", "salad"],
                )
            except Exception as exc:
                log(f"Salad reallocate failed: group={name} instance={instance_id} {reason} error={type(exc).__name__}: {exc}")
        if not use_worker_fallback:
            continue
        gpu = (cfg.get("worker_gpu_names", {}) or {}).get(worker_name) or salad_group_gpu(group, cfg)
        min_hash = gpu_map_value(gpu, cfg.get("min_hashrate_th", {}), cfg.get("default_min_hashrate_th"))
        if min_hash is None:
            continue
        info = worker_hashrates.get(worker_name)
        if not info and not bool(cfg.get("missing_worker_as_zero", True)):
            continue
        hashrate_th = float((info or {}).get("hashrate_th") or 0)
        entry["last_hashrate_th"] = round(hashrate_th, 3)
        entry["gpu"] = gpu
        entry["worker"] = worker_name
        entry["group"] = name
        entry["running_count"] = running_count
        if hashrate_th >= float(min_hash):
            entry.pop("low_since_epoch", None)
            entry.pop("low_reason", None)
            continue
        reason = f"hashrate={hashrate_th:.2f}TH<{float(min_hash):.2f}TH gpu={gpu or 'unknown'}"
        if not entry.get("low_since_epoch"):
            entry["low_since_epoch"] = now_ts
            entry["low_reason"] = reason
            log(f"Salad low hashrate observed: group={name} worker={worker_name} {reason}")
            continue
        duration = now_ts - float(entry["low_since_epoch"])
        if duration < low_seconds:
            continue
        last_reallocate = float(entry.get("last_reallocate_epoch") or 0)
        if now_ts - last_reallocate < cooldown_seconds:
            continue
        try:
            instances = list_salad_instances(config, name)
            instance = next((x for x in instances if x.get("started") or x.get("ready")), None) or (instances[0] if instances else None)
            if not instance or not instance.get("id"):
                log(f"Salad reallocate skipped: group={name} worker={worker_name} no instance id found")
                continue
            instance_id = str(instance["id"])
            result = reallocate_salad_instance(config, name, instance_id)
            entry["last_reallocate_epoch"] = now_ts
            entry["last_reallocated_instance_id"] = instance_id
            entry.pop("low_since_epoch", None)
            log(f"Salad reallocated low-hashrate instance: group={name} worker={worker_name} instance={instance_id} {reason} result={result}")
            notify(
                config,
                "Salad instance reallocated",
                f"{name} worker={worker_name} {gpu or ''} instance={instance_id} {reason}",
                priority="high",
                tags=["warning", "salad"],
            )
        except Exception as exc:
            log(f"Salad reallocate failed: group={name} worker={worker_name} {reason} error={type(exc).__name__}: {exc}")


def run_once(config, state, live):
    run_vast_cycle(config, state, live)
    run_runpod_cycle(config, state, live)
    run_tensordock_cycle(config, state, live)
    run_salad_cycle(config, state, live)


def provider_intervals(config):
    defaults = {"vast": 2, "runpod": 10, "tensordock": 5, "salad": 30}
    configured = config.get("provider_intervals_seconds", {})
    return {name: int(configured.get(name, defaults[name])) for name in defaults}


def run_provider_loop(config, state, live):
    intervals = provider_intervals(config)
    providers = {
        "vast": run_vast_cycle,
        "runpod": run_runpod_cycle,
        "tensordock": run_tensordock_cycle,
        "salad": run_salad_cycle,
    }
    next_run = {name: 0.0 for name in providers}
    futures = {}
    lock = Lock()
    last_err_log = {}
    err_log_interval = 300  # 同类 cycle error 每 5 分钟最多打一条/平台

    def submit(executor, name):
        def task():
            providers[name](config, state, live)
            with lock:
                save_json(STATE_PATH, state)
        futures[executor.submit(task)] = name

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as executor:
        while True:
            now_ts = time.monotonic()
            done = [future for future in futures if future.done()]
            for future in done:
                name = futures.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    if now_ts - last_err_log.get(name, 0.0) >= err_log_interval:
                        log(f"{name} cycle error: {type(exc).__name__}: {exc}")
                        last_err_log[name] = now_ts
                next_run[name] = time.monotonic() + intervals[name]
            running = set(futures.values())
            for name in providers:
                if name in running:
                    continue
                if now_ts >= next_run[name]:
                    submit(executor, name)
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="Vast/RunPod low-price GPU sniper for pearl-miner.")
    parser.add_argument("--config", default=str(ROOT / "config.local.json"))
    parser.add_argument("--live", action="store_true", help="Actually rent matched GPUs. Without this, dry-run only.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json(config_path, None)
    if config is None:
        raise SystemExit(f"Config not found: {config_path}")
    state = load_json(STATE_PATH, {"seen": {}, "rented": []})
    mode = "LIVE" if args.live else "DRY-RUN"
    log(f"Starting sniper mode={mode} config={config_path}")
    if not args.once:
        intervals = provider_intervals(config)
        log(f"Concurrent provider scanner enabled: vast={intervals['vast']}s tensordock={intervals['tensordock']}s runpod={intervals['runpod']}s")
        run_provider_loop(config, state, args.live)
        return
    while True:
        try:
            run_once(config, state, args.live)
            save_json(STATE_PATH, state)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log(f"Loop error: {type(exc).__name__}: {exc}")
        if args.once:
            break
        time.sleep(int(config.get("poll_seconds", 20)))


if __name__ == "__main__":
    main()
