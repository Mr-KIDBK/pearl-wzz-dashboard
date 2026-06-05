# 今晚挖珍珠 · Pearl Sniper Dashboard

多平台 **GPU 自动抢租挖 $pearl** + **网页看板** 统一管理。

在 **Vast.ai / RunPod / TensorDock / Salad** 上自动扫描 GPU 价格,低于阈值就租下、跑 PearlHash 矿机挖 **$pearl**,持续监控算力,对低效 / 不挖的机器自动销毁拉黑控成本——全程用一个**暗色网页看板**查看与操作。

> ⚠️ 会真实花钱。首次先 dry-run(不加 `--live`)看日志,确认无误再小额实跑。

---

## 网页看板

- **总览**:钱包、在跑机器数、总算力(矿池实测)、累计租金、待结算/已结算 $pearl,以及**按账号**列出在跑机器(单价/时长/算力 + Salad「组」列 + 一键关闭)——**每个账号一个卡片**,卡片右上显示**账户余额**(Vast / RunPod 自动拉取;Salad / TensorDock 无余额 API,点余额处 ✎ 直接填一次当前余额,看板即按消耗递减显示「估算余额 · 约 Yh 花完」)。
- **配置**:左侧栏「公共配置」+ **按账号**列出(可分别改每个账号)——网页直接改 **API key、钱包、GPU 型号与价格/算力门槛、各项参数**(结构化表单 + 高级 raw JSON),**暂停/启动租用**,**重启应用**,以及**修改看板登录密码**。
- **多账号**:同一平台可配多个账号(如 2 个 Salad + 2 个 RunPod),各自独立监控/抢卡(见下「多账号」章节)。
- 纯 Python 标准库,**零依赖**;密码门保护。

---

## 快速开始

只有**一个配置文件 `.env`**(平台 API key + 看板登录都在里面),**一条命令起 / 停全部服务**。

```bash
# 0. 依赖: 只需 Python 3.10+(纯标准库,无需 pip,也不需要 byobu)

# 1. 复制模板(真实文件已被 .gitignore 保护)
cp .env.example .env
for p in vast runpod tensordock salad; do cp configs/config.$p.example.json configs/config.$p.json; done

# 2. 改两处:
#    ① .env : 填平台 API key + 改 DASHBOARD_PASSWORD(看板登录密码,默认 123456 务必改掉)
#    ② 所有 config.*.json 的 prl_address 改成【你自己的 $pearl 钱包】

# 3. 一条命令起全部(4 平台 live 抢卡 + 网页看板)
#    Linux / macOS:
bash scripts/start-all.sh
#    Windows(PowerShell):
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1

# 一条命令停全部
#    Linux / macOS:
bash scripts/stop-all.sh
#    Windows(PowerShell):
powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1

# 4. 浏览器访问  http://<服务器IP>:8787 (Windows 本机用 http://localhost:8787)
#    登录 admin / 你设的 DASHBOARD_PASSWORD
```

> 钱包、key、密码、GPU 门槛等都能在看板**配置页**里改;改完点「重启应用」生效。
> 看板里还能**查看各平台后台日志**、暂停/启动租用、一键关闭某台机器。

---

## 必须配置(否则白挖 / 跑不起来)

| 项 | 说明 |
|----|------|
| `prl_address`(每份 config)| **你自己的 $pearl 钱包**,不改 = 挖给别人 |
| `.env` 的 API key | 启用平台的(VAST / RUNPOD / TENSORDOCK / SALAD)|
| `.env` 的 `DASHBOARD_PASSWORD` | 看板登录密码,**默认 `123456`,公网端口务必改掉** |
| `max_active_instances` / `max_total_hourly_usd` | 花钱护栏,**先设小**(注意:**每平台独立计算**,非全局——4 平台各跑独立进程/独立 state,最坏情况是 `平台数 × 上限`;Salad 受其 group replica 数管,不计入这两项)|

Salad 需在其后台预建 container group(env 填你的钱包)+ `SALAD_API_KEY`;TensorDock 需在 `keys/` 放 SSH 密钥对。

---

## 多账号(同平台多个账号)

每个平台可配**多个账号**,各自独立监控/抢卡、`state.*`/`logs/*` 隔离、护栏各算各的。`start-all` / `stop-all` 与看板会自动发现所有账号——**加一个账号零代码改动**:

```bash
# 例: 加第 2 个 Salad 账号
cp configs/config.salad.json configs/config.salad-2.json     # 文件名加后缀 -2
#   改 config.salad-2.json:
#     "api_key_env": "SALAD_API_KEY_2"        ← 指向第 2 个 key
#     salad.organization_name / project_name  ← 改成账号 2 的
echo 'SALAD_API_KEY_2=<账号2 的 key>' >> .env                 # .env 加对应 key
bash scripts/stop-all.sh && bash scripts/start-all.sh        # 重启, 看板自动多出该账号卡片
```

- **命名约定**:`config.<平台>.json` = 账号 1;`config.<平台>-<N>.json` = 账号 N。`.env` 里对应 key 用 `<标准名>_<N>`(如 `RUNPOD_API_KEY_2`),由 config 的 `api_key_env` 字段指向。
- **账号标签**自动按「平台-标识」显示(Salad 用组织名,如 `salad-duffett` / `salad-mrkidbk`);想自定义在 config 加 `"account_label": "..."`。
- **同钱包多账号**:各账号 `prl_address` 可相同,但矿池 worker 名 / Salad 容器组名要全局不冲突(如各账号用不同 `worker_prefix`、不同组名)。
- **护栏按账号独立**:`max_active_instances` / `max_total_hourly_usd` 各账号各算,最坏总花费 = 各账号上限之和。
- 注意:Salad「暂停租用」是**平台级**(sniper 按平台读暂停标记),同平台多账号会联动。

---

## 安全

- 看板在 `服务器:端口` 上、能填 key + 启停真实租机,**唯一防线是密码——务必改掉 `.env` 里默认的 `DASHBOARD_PASSWORD=123456`**。
- `.gitignore` 已保护 `.env`(含 key + 看板密码)/ `keys/` / 真实 `config.*.json`(含钱包)/ `state.*.json` / `logs/` / `docs/`,不会被提交。
- 实际挖矿用第三方矿机镜像(`kuzigmgm/pearl-miner`),使用即信任该来源与 PearlHash 项目。

---

> 详细部署/调参/各平台说明在本地 `docs/`(不随仓库分发)。
