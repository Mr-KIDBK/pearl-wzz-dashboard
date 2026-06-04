# 算力监控与自动止损机制

sniper 租机后会持续盯每台机器在 **PearlHash 矿池**上的真实算力,**算力不足就自动销毁实例 + 拉黑**,避免空转烧钱。三家平台（Vast / RunPod / TensorDock）共用同一套判定逻辑（`sniper.py:555 apply_low_efficiency_policy`）。

> ⚠️ **只在 `--live` 跑、且该平台 `create_enabled: true` 时才生效**（监控在 reconcile 路径里）。dry-run 不会动任何机器。

---

## 两层机制

### 第 1 层：零算力自救重启（先救）
`hashrate_zero_recover_seconds = 120`：算力为 0 持续 120s，先**重启矿机进程**而不是直接销毁。
- TensorDock：systemd 看门狗 `systemctl restart pearl-miner.service`（`sniper.py:1414`）。
- Vast / RunPod：靠 Docker 容器自身重启。
- 重启后能出算力就保留，不进入第 2 层。

### 第 2 层：低效销毁（救不回来就止损）
判定「低效」（满足任一即是）：
```
efficiency = 实测算力(TH) ÷ 价格($/h)
低效 = efficiency < min_th_per_usd_hour   或   算力 < min_hashrate_th[该卡]
```
按当前配置：
- `min_th_per_usd_hour = 334`（性价比红线）
- `min_hashrate_th`：**RTX 4090 < 200 TH/s** 或 **RTX 5090 < 300 TH/s** 即低效

低效**持续**超过 `low_efficiency_stop_seconds` → **销毁实例 + 拉黑该机器/offer**（之后不再租它）：

| 平台 | 销毁动作 | 低效持续阈值 | 启动宽限 `hashrate_grace_seconds` |
|------|---------|------------|----------------------------------|
| **RunPod** | `delete_runpod_pod`（删 pod） | **300s** | 180s |
| **Vast** | `destroy_vast_instance` | 900s | 300s |
| **TensorDock** | `delete_tensordock_instance` | 900s | 300s |

- 检查频率：`hashrate_watch_interval_seconds = 30s`。
- 算力来源：PearlHash API `connected_workers` 里该 worker 的真实算力（`sniper.py:170`），不是本地猜测。
- 销毁后会 `notify`（若配了 `alert_url`）并把机器写入 `state.json` 黑名单。

---

## ⚠️ 三个要紧的边界

1. **彻底掉线的机器杀不掉**：worker 必须在 PearlHash 上**有上报**才会被判低效。若一台机器完全不在线 / 查不到（`sniper.py:798` `if not info: continue`），**不会**触发自动销毁——能连池但算力低 → 会被杀；彻底掉线不上报 → 反而是盲区，得手动删或等平台回收。
2. **RunPod 止损更激进**：`backdate_low_efficiency_for_existing: true`，低效计时从「实例创建时刻」起算（`sniper.py:572`），一台从一开始就低效的 pod 可能**不等满 300s 就被立即销毁**。
3. **首次观测不立刻杀**（非 RunPod）：Vast/TensorDock 第一次发现低效只记录起点、返回（`sniper.py:577`），要等下一次检查且累计时长达标才销毁。

---

## 调参建议
市场行情变化时，同步调 `thresholds`（建机筛价）和 `min_hashrate_th` / `min_th_per_usd_hour`（租后止损线）。三者要匹配：阈值价定太高、算力线定太低 → 容易留下低性价比机器；反之 → 容易频繁建了又杀、浪费启动成本。
