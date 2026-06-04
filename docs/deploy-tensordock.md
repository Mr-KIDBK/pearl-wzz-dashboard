# TensorDock 部署（pearl-miner）

## 部署方式：🖥️ 裸服务器（Ubuntu VM + systemd），不是 Docker

和 Vast/RunPod 不同，TensorDock 这里**不跑 Docker 镜像**。sniper 用 OS 镜像 `ubuntu2404` 开一台**裸虚拟机**，再通过 cloud-init 脚本：装 NVIDIA 驱动 → 下载矿机二进制 → 写一个 `pearl-miner.service` 的 **systemd 服务**常驻运行，并带一个算力看门狗。

> 证据：`sniper.py:1475` `"image": cfg.get("image", "ubuntu2404")`（OS 镜像，非 docker 镜像）；cloud-init 在 `sniper.py:1244-1345` 写 `/etc/systemd/system/pearl-miner.service`。

> 🔴 **重要坑**：cloud-init 下载的是 `**pearl-miner-v8`**（`sniper.py:1309`），不是 v11——版本不一致，算力/特性可能不同，5090 兼容性也未必和 v11 一样。若要统一用 v11，手动改 `sniper.py:1309` 的下载 URL 为 `.../pearl-miner-v11`。

---

## A. 用 sniper 自动抢租（默认会真建机）

`config.tensordock.json` 里 `enabled = true`、`create_enabled = true`——`**--live` 即真实下单**，最需谨慎。

```bash
cd ~/gpu-sniper-shareable

# 演练
./scripts/run-tensordock.sh
./scripts/run-tensordock.sh --once

# 实跑（真建 VM、扣费）
./scripts/run-tensordock.sh --live

python3 sniper.py --config configs/config.tensordock.json --live
```

前置：

- `.env` 填 `TENSORDOCK_API_TOKEN`。
- **必须有 SSH 密钥对**（sniper 用它登录读日志）：`gpu-sniper-shareable/keys/tensordock`（私钥）+ `keys/tensordock.pub`（公钥）。生成：
  ```bash
  ssh-keygen -t ed25519 -N "" -f keys/tensordock
  ```

## B. 在 TensorDock 网页手动部署（自己开 VM 跑）

1. dashboard.tensordock.com → 部署一台 **Ubuntu 24.04** GPU VM（**RTX 4090**，2 vCPU / 4 GB / 100 GB）。
2. 用你的私钥 SSH 登录：`ssh -i <私钥> user@<ip> -p <port>`。
3. 按裸服务器流程跑矿机——见 `**docs/server-deploy.md`**（装驱动 + 下二进制 + systemd / tmux）。

---

## 选卡阈值（config.tensordock.json）

当前配置**只保留 4090 / 5090**（其余卡型已移除）：


| 卡型       | 阈值 $/h | TensorDock slug          | 最低算力 TH/s                    |
| -------- | ------ | ------------------------ | ---------------------------- |
| RTX 5090 | 0.55   | geforcertx5090-pcie-32gb | 300 ✅ 可用（driver 595→580，需够新） |
| RTX 4090 | 0.35   | geforcertx4090-pcie-24gb | 200 ✅ 最稳                     |


规格下限：vCPU ≥ 2、RAM ≥ 4 GB、存储 ≥ 100 GB、整卡。
地域：`location_rules` 现为空 `{}`，4090/5090 **不限地域**。

> 注意：顶层的 `tensordock.city = "Dallas"` 和 `excluded_states = []` 代码不读，只有 `location_rules` 与全局 `excluded_states` 生效。要限定地域得写进 `location_rules`。

成本/算力护栏：`min_th_per_usd_hour = 334`、`min_hashrate_th`（5090≥300/4090≥200）、启动超 900s 无算力判失败、低效 900s 销毁。→ 完整算力监控/止损机制见 `[hashrate-monitor.md](hashrate-monitor.md)`。
驱动：按卡型尝试 `nvidia_driver_packages_by_gpu`（4090 → 580→570；5090 → 595→580）。