# Vast.ai 部署（pearl-miner）

## 部署方式：🐳 Docker 镜像直跑
Vast.ai 实例本身就是从 Docker 镜像启动的。sniper 把 config 里的 `image`（`docker.io/kuzigmgm/pearl-miner:v11`）直接交给 Vast，由镜像的 `entrypoint.sh` 读环境变量启动矿机。**不需要登录服务器、不需要装驱动**——宿主机提供 NVIDIA 驱动，镜像自带 CUDA 12.8 运行库。

> 证据：`sniper.py:378` `"image": config["image"]`；矿机参数通过环境变量 `PRL_HOST / PRL_ADDRESS / PRL_WORKER` 注入（`sniper.py:274-291`）。

---

## A. 用 sniper 自动抢租（推荐）

```bash
cd ~/gpu-sniper-shareable

# 1) 演练：只扫描打印命中，不花钱（先这样确认行为）
./scripts/run-vast.sh

# 2) 只扫一轮就退出（调试用）
./scripts/run-vast.sh --once

# 3) 实跑：真实下单租机器、扣费
./scripts/run-vast.sh --live

# 直接调用主程序（脚本只是封装，会自动加载 .env 里的 VAST_API_KEY）
python3 sniper.py --config configs/config.vast.json --live
```

前置：`.env` 里填好 `VAST_API_KEY`；首次 `chmod +x scripts/*.sh`。

## B. 在 Vast.ai 网页手动部署（不走 sniper）

1. console.vast.ai → 搜索 GPU（按下方阈值筛价）。
2. **Image**：`kuzigmgm/pearl-miner:v11`（仓库须为 Public）。
3. **Launch mode / Docker options**：用镜像默认 ENTRYPOINT，无需填命令。
4. **Environment variables**（覆盖默认钱包/worker 时才填）：
   - `PRL_HOST=84.32.220.219:9000`（EU/US 池）或 `129.226.55.135:9000`
   - `PRL_ADDRESS=prl1REPLACE_WITH_YOUR_OWN_WALLET_ADDRESS`
   - `PRL_WORKER=kuzi`（镜像会自动拼成 `kuzi-<hostname>`）
5. **Disk**：20 GB 足够。
6. Rent → 看实例 **Logs**，出现 `Login success!` 且 `Hashrate Total = ... H/s`（非 0）即成功。

---

## 选卡阈值（config.vast.json，最高可接受单价 $/h）

当前配置**只保留 4090 / 5090**（其余卡型已移除）：

| 卡型 | 阈值 | 最低算力 TH/s |
|------|------|--------------|
| RTX 5090 | 0.55 | 300 ✅ 可用（**需新驱动 ≥575/580+**，否则算力 0） |
| RTX 4090 | 0.35 | 200 ✅ 最稳 |

其它筛选：`min_reliability ≥ 0.9`、整卡 `min_gpu_frac = 1.0`、`max_offer_price ≤ 0.9`、偏好 US/CA。
成本红线 `min_th_per_usd_hour = 334`，租后算力/价格低于此值持续 900s 自动销毁拉黑。→ 完整算力监控/止损机制见 [`hashrate-monitor.md`](hashrate-monitor.md)。

> ✅ 5090 受 v11 支持，但**宿主机驱动要够新（≥575/580+ open）**才出算力；驱动太旧（如 570）会 0 H/s。Vast 选机时尽量挑驱动新的宿主，或在镜像/启动里确认驱动版本。详见 docker/SALAD.md。
