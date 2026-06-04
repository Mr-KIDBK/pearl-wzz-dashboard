# GPU Sniper（$pearl 挖矿抢租机器人）

在 **Vast.ai / RunPod / TensorDock** 三家算力平台上自动扫描 GPU 价格，找到低于设定阈值的显卡（RTX 3080/3090/4090/5090 等）就自动租下，在机器上运行 **PearlHash 矿机**给 **$pearl 币**挖矿，并持续监控算力，对效率过低 / 启动失败的机器自动销毁并拉黑以控制成本。

> ⚠️ **这是会真实花钱的自动租机工具。** 首次使用务必先 dry-run（不加 `--live`）观察日志，确认行为符合预期后再小额实跑。详见文末「风险与安全提示」。

---

## 目录结构

```
gpu-sniper-shareable/
├── sniper.py                      # 主程序（扫描 / 租用 / 监控 / 销毁）
├── dashboard.py                   # 网页看板（总览 + 配置，stdlib 零依赖）
├── .env.example                   # API key 模板 → 复制成 .env 填入
├── dashboard.conf.example.json    # 看板密码/端口模板 → 复制成 dashboard.conf.json
├── configs/
│   ├── config.*.example.json      # 4 平台配置模板（提交进仓库的范例）
│   └── config.{vast,runpod,tensordock,salad}.json  # 真实配置（.gitignore，含你的钱包）
├── scripts/
│   ├── run-{vast,runpod,tensordock,salad}.sh / .ps1 # 单平台启动
│   ├── start-all.sh / stop-all.sh                   # byobu 一键起/停全部平台
│   └── run-dashboard.sh                             # 启动网页看板
└── docs/                          # 各平台部署详解 + 算力监控
    ├── deploy-vast.md / deploy-runpod.md / deploy-tensordock.md
    └── hashrate-monitor.md        # 算力不足自动销毁止损（跨平台）
```

> `.gitignore` 已保护 `.env` / `dashboard.conf.json` / `keys/` / `state.*.json` / `logs/` / 真实 `config.*.json`（含你的钱包）等敏感文件，不会被提交。

> 各平台「部署方式（Docker / 裸服务器）、运行命令、选卡阈值」详见 `docs/deploy-*.md`。
> 「算力不足如何自动关机释放资源」详见 `docs/hashrate-monitor.md`。
> 自己拿裸服务器手动跑矿机见仓库根目录 `../docs/server-deploy.md`；Docker 镜像构建/Salad 部署见 `../docker/SALAD.md`。

运行后会在本目录生成 `state.json`（租用记录、黑名单）和 `sniper.log`（日志）。**这两个文件含实例 ID 等运营信息，不要外传。**

---

## 🚀 快速开始（5 步初始化）

```bash
# 1. 复制模板（真实文件已被 .gitignore 保护，不会提交）
cp .env.example .env
cp dashboard.conf.example.json dashboard.conf.json
for p in vast runpod tensordock salad; do cp configs/config.$p.example.json configs/config.$p.json; done

# 2. 填 .env：把你要用的平台 API key 填进去
# 3. 改钱包：把所有 config.*.json 里的 prl_address 改成【你自己的 $pearl 钱包】
#    （看板「配置 → COMMON」一键写全 4 份；命令行可用 sed 批量替换占位符）
# 4. 改 dashboard.conf.json 里的密码
# 5. 先 dry-run 验证，再 --live 实跑
bash scripts/run-vast.sh --once          # 单平台演练（不下单）
bash scripts/start-all.sh                # byobu 一键起全部 live
bash scripts/run-dashboard.sh            # 网页看板（默认 8787 端口）
```

### ⚙️ 必须 / 建议配置清单

| 优先级 | 配置项 | 说明 |
|--------|--------|------|
| 🔴 **必须** | `prl_address`（4 份 config）| **你自己的 $pearl 钱包**，不改 = 挖给别人 |
| 🔴 **必须** | `.env` 的 API key | 启用平台的（VAST / RUNPOD / TENSORDOCK / SALAD）|
| 🔴 **必须** | `dashboard.conf.json` 密码 | 看板是公网端口，别留默认密码 |
| 🔴 Salad 用 | `salad.organization_name`/`project_name`/`include_container_groups` | 且在 Salad 后台建好 container group、其 env 填**你的钱包** |
| 🔴 TD 用 | `keys/tensordock`(.pub) | 自己的 SSH 密钥对 |
| 🟡 建议 | `max_active_instances` / `max_total_hourly_usd` | **花钱护栏，先设小** |
| 🟡 建议 | 各平台 `enabled` / `create_enabled` | 开哪些平台、是否自动建机 |
| 🟡 建议 | `thresholds`（最高 $/h）/ `min_hashrate_th`（最低 TH/s）| 每卡型的价格 & 算力门槛 + 目标 GPU |
| 🟡 建议 | `min_th_per_usd_hour` | 效率门槛（TH per $·h）|
| 🔵 平台特定 | runpod `cloud_types`/`country_codes`；tensordock `gpu_slugs`/`nvidia_driver_packages`（5090 需驱动 ≥575/580）/`city`；salad alphapool 选项 | 见各 `docs/deploy-*.md` |
| ⚪ 可选 | `alert_url`、监控/扫描节奏、blacklist 参数 | 调优用 |

> 以上都能在**网页看板的「配置」页**编辑（结构化表单 + 高级 raw JSON），保存后点「重启应用」生效。

---

## 一、安装

1. 安装 **Python 3.10+**（脚本仅用标准库，无需 `pip install`）。
2. 复制密钥模板并填入你要用的平台 API key：
   ```bash
   cp .env.example .env
   ```
   `.env` 内容：
   ```
   VAST_API_KEY=替换为你的_vast_api_key
   RUNPOD_API_KEY=替换为你的_runpod_api_key
   TENSORDOCK_API_TOKEN=替换为你的_tensordock_api_token
   ```
   只需填你打算用的那一家，其余可留空。

---

## 二、配置（运行前必改）

编辑你要用的那份 `configs/config.<provider>.json`，至少替换以下项：

| 字段 | 说明 |
|------|------|
| `prl_address` | **你的 $pearl 收款钱包地址**（默认是占位符 `<YOUR_PRL_WALLET_ADDRESS>`，不改的话挖矿无效）|
| `prl_host` | PearlHash 矿池地址（默认 `84.32.220.219:9000`，如用其它池才改）|
| `max_active_instances` | 最多同时租几台（默认 `1`）|
| `max_total_hourly_usd` | 每小时总花费上限（默认 `1.0` 美元）|
| `thresholds` | 各卡型可接受的最高单价（$/小时），高于此价不租 |
| `alert_url` | 选填，填 ntfy 推送地址可在租用/销毁时收到通知 |

**TensorDock 额外需要 SSH 密钥对**（用于登录租来的机器读日志）：在 config 的 `tensordock` 段填 `ssh_key_path`（公钥）和 `ssh_private_key_path`（私钥）。可用 `ssh-keygen` 生成。

成本红线 `min_th_per_usd_hour: 334`（约 0.003 USD/TH-小时）：租后实测「算力 ÷ 价格」低于此值并持续 `low_efficiency_stop_seconds` 秒，就会自动销毁该机器。市场变化时，请同步调整 `thresholds` 和 `min_hashrate_th`。

---

## 三、运行命令

脚本默认是 **dry-run（演练）模式**：只扫描、打印命中日志，**不会真的租机器、不花钱**。加 `--live`（Linux）或 `-Live`（PowerShell）才会真实下单。

### Linux / macOS

```bash
# 演练（推荐先这样跑，确认没问题）
./scripts/run-vast.sh
./scripts/run-runpod.sh
./scripts/run-tensordock.sh

# 实跑（真实租机器、扣费）
./scripts/run-vast.sh --live
./scripts/run-runpod.sh --live
./scripts/run-tensordock.sh --live

# 只扫描一轮就退出（适合调试）
./scripts/run-vast.sh --once
./scripts/run-vast.sh --live --once
```

首次运行前给脚本加执行权限：
```bash
chmod +x scripts/*.sh
```

### Windows（PowerShell）

```powershell
# 演练
.\scripts\run-vast.ps1
.\scripts\run-runpod.ps1
.\scripts\run-tensordock.ps1

# 实跑
.\scripts\run-runpod.ps1 -Live

# 只扫一轮
.\scripts\run-vast.ps1 -Once
.\scripts\run-vast.ps1 -Live -Once
```

### 直接调用主程序（脚本只是封装）

```bash
python3 sniper.py --config configs/config.vast.json            # dry-run 持续轮询
python3 sniper.py --config configs/config.vast.json --live     # 实跑
python3 sniper.py --config configs/config.runpod.json --once   # 单轮
```

参数说明：
- `--config <file>`：指定使用哪份平台配置（必填）。
- `--live`：真实租用机器（不加则只演练）。
- `--once`：只扫描一轮后退出（不加则持续循环，按各平台间隔轮询）。

运行脚本会自动加载本目录 `.env` 里的 API key 到环境变量，然后调用对应 config 启动 `sniper.py`。

---

## 四、各平台默认状态

| 平台 | `enabled` | 是否会自动建机 | 备注 |
|------|-----------|----------------|------|
| **Vast** | ✅ | `--live` 时会租 | 读容器日志判断算力 |
| **RunPod** | ✅ | ❌（`create_enabled=false`，默认仅观察价格）| 想自动建机需手动改为 `true` |
| **TensorDock** | ✅ | ✅（`create_enabled=true`，`--live` 即下单）| 最需谨慎，需 SSH 密钥 |

---

## 五、风险与安全提示

- **会真实花钱**：`--live` 下会按市场价租机器扣费。TensorDock 默认就会自动建机，最需小心。
- **先小额验证**：保持 `max_active_instances` 和 `max_total_hourly_usd` 较低，首次 `--live` 全程人工盯几次部署后再放手。
- **花费护栏靠本地记账**：上限通过 `state.json` 计算，删除该文件或多开进程可能导致记账失准、超额租用。
- **确认钱包地址**：务必把 `prl_address` 设为你自己的钱包，否则等于白干。
- **闭源矿机**：实际挖矿用的是第三方二进制（Docker 镜像 `conishc/pearl-miner` 与 `pearlhash.xyz/downloads/pearl-miner-v8`），本仓库无法验证其内部行为——使用即信任该来源与 PearlHash 项目，请自行评估 $pearl 的价值与可提现性。
- **不要外传** `state.json` 和 `sniper.log`，它们含实例 ID 与操作历史。

---

## 六、Salad + AlphaPool（可选，默认休眠）

本仓库额外集成了 **Salad** 容器云的监控与 **AlphaPool** 第二矿池支持，默认 `salad.enabled=false` **完全休眠**，不影响其它三个平台。

与 Vast/RunPod/TensorDock 不同，**Salad 是「监控 + 故障迁移」而非「自动租用」**：它不创建实例，只对你已经在 Salad 后台创建并启动的 **container group** 做算力监控，发现低效就 `reallocate`（在 group 内迁移到别的机器，无需取消重租）。

启用步骤：
1. 在 Salad 后台预先创建并启动 container group（运行 pearl-miner 镜像，设好 `PRL_WORKER` 等环境变量）。
2. `.env` 增加 `SALAD_API_KEY=...`。
3. 编辑 `configs/config.salad.json`：
   - `salad.enabled` 改为 `true`
   - 填 `organization_name` / `project_name`
   - `include_container_groups` 填要监控的 group 名（留空=全部）
   - 低效阈值用 `min_hashrate_th`（按卡型）/ `low_efficiency_stop_seconds` / `reallocate_cooldown_seconds`
4. （可选）启用 AlphaPool 矿池侧算力校验：`alphapool_worker_api_enabled=true` + `alphapool_min_hashrate_th`。
5. 运行：`bash scripts/run-salad.sh --live`（或 `start-all.sh` 在 `salad.enabled=true` 时会自动多开一个 salad 窗口）。

> 没有 Salad 账号/group 时保持 `enabled=false` 即可，能力就位待启用。
