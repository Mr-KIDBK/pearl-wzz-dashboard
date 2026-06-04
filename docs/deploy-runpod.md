# RunPod 部署（pearl-miner）

## 部署方式：🐳 Docker 镜像直跑
RunPod 的 Pod 从 Docker 镜像启动。sniper 把 config 的 `image` 作为 `imageName` 交给 RunPod，镜像 `entrypoint.sh` 读环境变量启动矿机。**无需登录服务器、无需装驱动。**

> 证据：`sniper.py:937` `"imageName": config["image"]`。

> ⚠️ **默认仅观察、不自动建机**：`config.runpod.json` 里 `create_enabled = false`、`observe_enabled = true`——sniper 只扫描价格、打印命中，不会真的开 Pod。要自动建机需手动改 `create_enabled: true`。

---

## A. 用 sniper（默认观察价格）

```bash
cd ~/gpu-sniper-shareable

# 演练 / 观察
./scripts/run-runpod.sh
./scripts/run-runpod.sh --once

# 实跑（仅当你已把 create_enabled 改为 true 才会真建机）
./scripts/run-runpod.sh --live

python3 sniper.py --config configs/config.runpod.json --live
```

前置：`.env` 里填 `RUNPOD_API_KEY`。

## B. 在 RunPod 网页手动部署（不走 sniper）

1. runpod.io → **Deploy a Pod**（Community Cloud 更便宜 / Secure Cloud 更稳）。
2. 选 GPU（按下方阈值筛价；4090 最稳，5090 也可用但要确认宿主驱动 ≥575/580+）。
3. **Container Image**：`kuzigmgm/pearl-miner:v11`（Public）。
4. **Container Start Command**：留空（用镜像 ENTRYPOINT）。
5. **Environment Variables**（覆盖时填）：
   - `PRL_HOST=84.32.220.219:9000`（EU/US）或 `129.226.55.135:9000`
   - `PRL_ADDRESS=prl1REPLACE_WITH_YOUR_OWN_WALLET_ADDRESS`
   - `PRL_WORKER=kuzi`
6. **Container Disk** 20 GB；不需要 Volume / 不需要暴露端口（挖矿只出站）。
7. Deploy → 看 Pod **Logs**：`Login success!` + `Hashrate Total = ... H/s`（非 0）= 成功。

---

## 选卡阈值（config.runpod.json，注意卡名是 RunPod 全称）

当前配置**只保留 4090 / 5090**（其余卡型已移除）：

| 卡型 | 阈值 $/h | 最低算力 TH/s |
|------|---------|--------------|
| NVIDIA GeForce RTX 5090 | 0.55 | 300 ✅ 可用（**需新驱动 ≥575/580+**，否则算力 0） |
| NVIDIA GeForce RTX 4090 | 0.35 | 200 ✅ 最稳 |

其它筛选：`country_codes = [US, CA]`（但 `unrestricted_country_gpu_keywords = [4090, 5090]` 表示 4090/5090 不限国家）、`min_download 50 / upload 20 Mbps`、`min_ram_per_gpu 8G`、`min_vcpu_per_gpu 2`、`cloud_types = [COMMUNITY, SECURE]`。
成本红线 `min_th_per_usd_hour = 334`，低效 `low_efficiency_stop_seconds = 300s` 后销毁（RunPod 止损最快，且从创建时刻起算）。→ 完整算力监控/止损机制见 [`hashrate-monitor.md`](hashrate-monitor.md)。
