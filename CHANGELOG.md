# Changelog

本文件记录「今晚挖珍珠 · Pearl Sniper Dashboard」的重要变更。

## [Salad scid 健康检测 + 半自动弹窗重登] — 2026-06-17

dashboard 启动/运行时检测 salad portal 会话(scid)缺失或过期,自动弹有头浏览器引导人工重登,登录完成自动续上抓取——不再需要手动盯着重跑 salad_login.py。

### Added — 新增
- **scid 健康检测**:`should_relogin`(纯函数)判定——会话文件**缺失**或**连续 2 轮抓取全空**(scid 过期)且距上次重登尝试满冷却(默认 30min)→ 触发重登。启动时(缺失账号)+ 运行中(连续空)都检测。
- **半自动重登 `auto_login`**:弹有头 Chromium 到 portal,**自动轮询 portal-api 检测登录完成**(替代旧的 `input()` 等回车,后台 nohup 进程也能用);你人工过完 Turnstile/OTP、scid 生效后自动存会话、关窗、续上抓取;超时(默认 10min)放弃。
- run_manager 每账号维护 `consecutive_empty` 计数 + `last_relogin` 冷却,过期/缺失触发 auto_login 并用新会话重建 context。

### Changed — 变更
- `start_portal_manager` 不再因 session 文件缺失而跳过账号(改按 `salad.enabled`),缺失的账号交给 run_manager 引导登录。

### Notes — 注意
- **优雅降级**:playwright 缺失 / 无 GUI 环境(ssh/服务器)弹窗失败 → 回退现状(log 提示手动 `salad_login.py`),不崩。

---

## [Salad 逐实例低效判定加固] — 2026-06-12

修复多卡弹性组下低效判定的两个边界 bug,让死机/掉队机器被可靠清理。

### Fixed — 修复
- **死机(无容器日志 + 不在矿池)被漏判**:原逻辑两数据源都没有时直接跳过判定,致 0 算力死机不被清理。改为矿池 API 正常(能确认真离线)时视为 0 算力判低效,且双无 running 实例**绕过新实例长宽限**——首次观测后满 `low_efficiency_stop_seconds`(默认 5min)即 reallocate;矿池 API 也挂时仍跳过不杀(防抖动误杀)。
- **多卡组逗号串误归一**:salad 弹性组的 gpu 字段(`RTX 4090,RTX 5090,...`)被 `normalize_gpu` 拼串后命中首个 "5090" → 误取最严阈值(300)。改为含逗号即返回空,回退组级阈值。
- tests: `test_salad_pool_authoritative.py` 加 missing/绕宽限 case + 新增 `test_normalize_gpu_multi.py`。

---

## [挖矿成本指标 + 产出口径自重置] — 2026-06-11

新增「挖矿成本」量化每个 $PRL 的电租成本并对比币价提示盈亏;产出口径改为自重置,重置统计后真正归零。

### Added — 新增
- **挖矿成本卡(两指标)**:
  - 累计挖矿成本 `cost_cumulative_usd` = 累计租金 ÷ 累计产出(按当前矿池视图)。
  - 最近 3 小时实时成本 `cost_recent3h_usd` = (当前每小时租金 × 3) ÷ 最近 3h 产出(全局口径,不随视图变)。
  - 卡片两行各与实时币价对比:低于币价绿(盈利)/ 高于红(应关机);无数据显 —。
- **产出滚动快照**:每 5 分钟记一次、裁剪保留 4h,用于算「最近 3h 产出」(运行不足 3h 显 —)。

### Changed — 变更
- **产出口径改为「自重置」**:非 PearlHash 池产出 = 自重置增量(全期值 − 重置基线,基线含 pending),口径统一为 `since_reset`,消除合并视图「口径不一」警示。**重置统计后累计产出真正归零**(此前用全期值,重置不归零)。平均每小时产出、利润口径随之统一。

### Fixed — 修复
- **salad RTX 4080 SUPER 成本显示成价格区间**(如 `$0.090–0.250/h`):salad 组为 batch 优先级且 gpu-classes 无 4080 SUPER class,定价两路都查不到 → 回退组级区间。改为别名复用 salad RTX 4080 class 的实时(batch)价,显示真实单价。

---

## [herominers + pearlfortune 双矿池 + 算力趋势图 + 池详情] — 2026-06-10

新增 herominers、pearlfortune 两个矿池(共支持 4 池),默认切到 pearlfortune;池卡片加详情条与算力趋势图。

### Added — 新增
- **两个新矿池**:herominers、pearlfortune 注册接入(POOLS 驱动,共 PearlHash / TW Pool / herominers / pearlfortune 4 池),配置页可切换/一键迁移。
- **默认矿池改为 pearlfortune**(active_pool 兜底)。
- **池卡片详情条**:待结算、累计收益、份额(good/invalid/stale)、网络信息(高度/爆块)、worker 离线标记。
  - pearlfortune 接 ledger(已付/收益)+ pending/费率;herominers 接 shares + pool_info。
- **可折叠算力趋势图**:每池每小时算力折线(canvas);pf/twpool/herominers 显示,pearlhash/合并视图隐藏。
- 待结算余额计入累计产出;每小时产出改用「总产出 ÷ 周期」口径。

### Changed — 变更
- pearlfortune 默认镜像 v1.1.1 → **v1.1.2**(修复 Salad 加壳 miner PID1 崩溃无限重启)。
- salad 低效判定按机器所在池路由:pearlfortune 池权威 TH 门槛 / herominers 退容器日志。
- `parse_latest_hashrate` 支持 pearlfortune `proof_per_sec` 日志算力。

### Fixed — 修复
- herominers 余额改读 `stats.balance`(真实数据证实,修此前取不到余额)。

---

## [Salad portal 真实 GPU/余额 + 池权威逐实例低效] — 2026-06-09

通过浏览器会话从 Salad portal 抓真实单卡型号/单价/余额;salad 低效判定改为「矿池权威、逐实例」。

### Added — 新增
- **Salad portal 抓取**(常驻 headless Playwright,持登录会话):
  - 每实例**真实单卡 GPU 型号 + 单价**(公共 API 不返回 GPU,portal 是唯一来源)。
  - **账号真实余额**(前端显示「实时余额」,优先于手填估算)。
  - 一次性有头登录 `salad_login.py` 存会话,之后 headless 静默续期。
- **salad 累计租金改用 portal 真实余额下降量**(实测扣费;非-salad 仍 price×time)。
- **累计产出卡加「平均每小时产出」**(自重置口径)。
- 多矿池框架雏形:POOL_MONITORS 注册表 + build_summary/pool_view 跨池 POOLS 驱动。

### Changed — 变更
- **salad 低效判定改为「矿池权威、逐实例」**:矿池在线算力 ≥ 门槛=健康;曾在池出现却离线(即使容器日志在挖)即 reallocate;矿池 API 挂 / 无 machine_id / 新实例宽限期则退容器日志判定(防误杀)。
- salad twpool 镜像换成 `mrkidbk/pearl-miner-twpool:v1.9.1`(每实例唯一 worker;runpod/vast 向后兼容)。
- 日志算力↔实例改按 `machine_id` 关联(salad API instance_id 偶发 None,此前致算力显示 0)。
- 项目改用 **uv** 管理(pyproject + uv.lock + Python 3.14)。

### Fixed — 修复
- `_twpool_view` 剔除矿池上报的损坏算力(单 worker > 2000 TH/s)。
- `parse_latest_hashrate` 支持 twpool 镜像日志格式。

### Notes — 注意
- 新增 `scripts/restart-dashboard.sh` 安全重启看板(杀旧 → 等端口释放 → 起新 → 验证监听)。

---

## [机器分池可视化 + 全面分池计算] — 2026-06-08

总览所有指标可按矿池分别查看,机器表显示每台在哪个池并可按池筛选。

### Added — 新增
- **每台机器矿池判定**:镜像优先(conishc/twpool→TW Pool;kuzigmgm/mrkidbk/其它→PearlHash)→ 取不到镜像用「该 worker 在哪个池报算力」兜底 → 仍判不出=未知。
  - 镜像来源:salad 从 `salad_live` 组信息透出;runpod/vast 用 `account_machine_images` live 抓取 + serve-stale 缓存 + 后台刷新(迁移后镜像即时准确)。
- **机器表加「矿池」列** + 按顶部矿池下拉筛选:合并=全部;单池=只显示该池机器;未知机器只在合并显示。各账号当前 $/h 跟随筛选(只算该池机器)。
- **在跑台数按池**:合并显示 `PearlHash N / TW Pool M / 未知 K`;单池显示该池台数。
- **成本/利润/性价比按池**(随矿池下拉切换):
  - 当前 $/h:按每台机器的池拆分(精确)。
  - 累计租金:`tick_spend` 按池累计(`cumulative_usd_by_pool`,**自本次更新部署起**;历史混合段仅进总额;unknown 仅进总额不入单池)。
  - 累计折合利润:该池产出 − 该池租金(保留口径警示,twpool 全期产出 vs 自更新租金不完全对齐)。
  - **新增「算力性价比」卡**:TH/($·h) = 该池总算力 / 该池当前 $/h。

### Notes — 注意
- 不影响 PRL/USDT 币价与 K 线、各账号平台账单余额(账号级)、hours_left。
- 镜像/缓存均后台 30s 刷新;build_summary/tick_spend 读缓存不额外打 API。

---

## [配置页矿池澄清 + 迁移下拉] — 2026-06-08

消除多矿池相关的配置困惑,迁移目标改下拉选择。

### Added / Changed
- **COMMON 全局配置加只读「矿池参考」区**:列出每个池用的镜像 + 是否读 PRL_HOST(PearlHash → kuzigmgm 读 PRL_HOST;TW Pool → conishc 不读 host)。说明「镜像由所选矿池自动决定,切池/迁移无需改 image/prl_host」。
- **image / prl_host 字段加灰字标注**:image「仅未选矿池时兜底」、prl_host「仅 PearlHash 读, TW Pool 不读」——澄清这俩是 pearlhash 兜底值,换池不用改。
- **一键全部账号迁移改池下拉**:按钮旁加矿池下拉(PearlHash / TW Pool),不再手填池名;MIGRATE 确认保留。
- `build_full_config` 的 `pools` 列表补 `image`/`reads_prl_host` 字段(供前端矿池参考区用)。

---

## [总览矿池显示切换] — 2026-06-08

总览页加矿池显示切换下拉,迁移期间混合机群可按池查看。纯显示,不影响挖矿/配置/迁移。

### Added — 新增
- **总览矿池切换下拉**(钱包卡内):`合并 / PearlHash / TW Pool` 三选一,默认**合并**,`localStorage` 记忆(键 `pool_view`)。切换即时重渲染总览。
- **后端 `twpool_data()`**:查 `api.tw-pool.com/api/worker_stats`,serve-stale 缓存 + 后台 30s 刷新(与 pearlhash `pool_data` 并列)。
- **`pool_view(which)`**:pearlhash/twpool/merged 三视图统一映射(在挖 worker 表 / 总算力 / 矿池 PRL 余额 / error);合并按 worker 名取最大、总算力与余额相加、单池故障容错。
- **`/api/summary?pool=`**:按所选池返回 总算力 / worker 表 / 矿池余额 / 累计产出。
- **产出口径标注**(累计产出卡):PearlHash =「自重置起算(统计自 …)」、TW Pool =「全期(已付+未付)」、合并 =「PearlHash 自重置 + TW Pool 全期」。`tick_output` 始终调用,pearlhash 自重置累加不中断。
- **矿池余额卡**:显示所选池的 PRL 钱包余额(twpool 直接来自 API;无数据显示 —)。

### Notes — 注意
- 各账号**平台账单余额**(vast/runpod 充值)不随池切换变;随池变的是**矿池钱包 PRL 余额**。
- 不影响 PRL/USDT 币价与 K 线行情(与矿池无关)、不影响在跑机器表每实例算力(来自 sniper 双池合并)。

---

## [多矿池可切换 + 一键迁移] — 2026-06-08

支持 PearlHash / TW Pool 多矿池:双池监控避免混合机群误杀、可配置默认抢哪个池、UI 一键把现有 vast/runpod/salad 机器迁移到目标池。架构可扩展到更多池。所有第三方迁移接口均经真机/官方源码实测确认后才实现。

### Added — 新增
- **矿池注册表 `POOLS`**(sniper.py):pearlhash(`kuzigmgm/pearl-miner:v11`,读 PRL_HOST)/ twpool(`conishc/pearl-miner:twpool-v1.9.0-auto`,池写死、读 PRL_ADDRESS/PRL_WORKER)。加新池 = 加一条 registry + 一个 `*_worker_hashrates` adapter。`active_pool()` / `effective_image()` 决定新抢机器用的镜像。
- **双池监控(安全层)**:`twpool_worker_hashrates`(实测 `api.tw-pool.com/api/worker_stats`)+ `merged_worker_hashrates`(按 worker 名合并取最大、单池故障跳过)。runpod/vast/salad reconcile 改用合并算力 → 混合机群(部分在 pearlhash、部分在 twpool)算力都查得到,**不误杀**。
- **新抢矿池可切换**:每账号配置 `pool`(顶层),create 用 `effective_image`;配置页每账号矿池下拉(`/api/set-pool`,只改新抢、不迁移)。
- **一键迁移**(`/api/migrate`,确认词 `MIGRATE` 严格校验):
  - **runpod**:`POST /v1/pods/{id}/update` 原地换 imageName+env 触发 reset(实测确认 env 整体替换)。
  - **vast**:`DELETE` 销毁,扫描循环用新池镜像重租。
  - **salad**:`PATCH containers/{group}`(merge-patch+json)改 image+env(整体替换)→ Salad 自动重建实例 + 保守显式 recreate(实测 group gpu10 迁移成功)。
  - UI:每账号「迁移现有机器到所选池」按钮 + 全局「一键全部账号迁移」按钮,均经 `MIGRATE` 确认 prompt。
- **host 兜底池感知**:twpool 不读 PRL_HOST → 切 host 无意义,迁移到 twpool 后自动禁用 host 兜底。

### Notes — 注意
- 迁移只改现有机器 + 落盘 pool;**新抢用新池需重启对应账号监控才生效**。
- salad 现役镜像为 `mrkidbk/pearl-miner:latest`(用 `WORKER_NAME`),迁移取 `salad_group_worker_name` 沿用 worker 名。

---

## [行情图表 + 实时币价] — 2026-06-05

### Added
- **PRL/USDT K 线图面板**:总览页币价行下方可折叠行情面板(默认收起,点标题展开)。
  - 纯 Canvas 绘制(无外部库):Candlestick(绿涨/红跌)+ **EMA20(金)/EMA60(紫)** + 价格 Y 轴 + 时间 X 轴。
  - 底部独立 Canvas 成交量柱状图(颜色跟涨跌联动)。
  - 周期切换:**15m / 1h / 4h / 1d**;头部实时显示区间涨跌幅。
  - hover crosshair **OHLCV tooltip**。
  - `renderOverview` 每 10s 刷新后保持展开状态并自动重绘。
- **实时 PRL/USDT 币价**:自动从 SafeTrade REST API 拉取(`ticker.last`),后台每 30s 刷新;API 失败 fallback 到缓存旧值。看板显示「**● 实时**」标志,无需手动填写。移除了原手动「币价」输入框与「保存币价」按钮。
- 后端 `/api/kline?period=` 代理端点:serve-stale 缓存(各周期独立 TTL),API 失败返回旧缓存/空列表。

---

## [看板产出统计与体验改版] — 2026-06-05

围绕收益可视化与界面体验的一批改动（前端为主），与多账号合并在同一天完成。

### Added — 新增
- **累计产出 / 累计折合利润卡**：替换原「待结算 / 近期已结算 PEARL」两卡。产出 = 矿池正向 epoch credit（提现不计）+ 当前 pending，**自重置起算**（从 0 单调起涨，结算不跳变、提现不减）；折合利润 = 产出 × 币价 − 累计租金。
- **币价配置 + 重置统计**（仅 admin）：可填币价（默认 0.75）实时折算；一键重置租金/产出/利润从当前起算（保留币价）。
- **亮 / 暗双主题**：默认亮色，左下角透明切换钮，`localStorage` 持久化 + 防首屏闪烁。
- **文档页**：导航「文档」组 → 工具说明（含本地部署四步 + docker 拉取原理）+ 挖珠教程（小白四步：钱包 → 租卡 → 配置 → 卖币 SafeTrade）。
- **工具集**新增「交易平台」组（SafeTrade / Pearl OTC / OKX Web3）。
- 左下页脚加 **GitHub 项目链接**（与主题切换并排）。

### Changed — 变更
- 导航改名：总览 → **仪表盘**、工具链接 → **工具集**、配置 → **配置工作台**；favicon 去黑底透明、品牿字距加宽、菜单加极细分隔线、淡化选中背景；钱包卡按钮 ACCOUNT → 改 **PearlHash →**。
- 总览各平台租用情况**按在跑机器数排序**（有机器的账号在上）。

### Fixed — 修复
- **Salad 移动卡价格显示区间而非单价**：`gpu_key` 未剥结尾「 GPU」后缀（`RTX 5090 Laptop GPU`）致名字不匹配价表 → fallback 到整档 min–max 区间。
- **侧栏滚动到页面底部时 footer 上移**：侧栏 `position:sticky` 改 `fixed`，常驻左下。
- **配置侧栏账号项要进配置页才出现**：改由总览加载时即用 `/api/rentals` 填充。

## [多账号支持] — 2026-06-05

将单账号架构升级为「多平台 × 多账号」,并修复了一批 dashboard 显示缺陷。`sniper.py` 监控/抢卡核心**未改动**,改动集中在启动脚本、dashboard 与配置组织。

### Added — 新增
- **多平台多账号支持**:可为同一平台配置多个账号(`configs/config.<平台>-<N>.json` + `.env` 里的 `<KEY>_<N>`),各账号独立监控、抢卡、`state.*`/`logs/*` 隔离。**新增第 N 个账号零代码改动**——放一个 config 文件 + 在 `.env` 加对应 key + 重启即可。
- dashboard 总览页**按账号渲染**:每个账号一个卡片(独立 RUNNING/余额/机器表),顶部为合并汇总(同钱包)。
- dashboard 配置页**按账号**:左侧栏动态列出各账号,可分别编辑账号 1/2/… 的门槛、key、raw JSON。
- 所有操作(暂停租用 / 重启 / 关闭单机 / 保存配置)**按账号定位**到对应 config/进程;关闭/启动时按账号注入对应 key。
- Salad 实例表格新增「组」列,显示 Container Group 名(gpu1…gpuN)。
- 账号标签采用「平台-标识」格式(Salad 自动用组织名,如 `salad-duffett` / `salad-mrkidbk`;其它平台可在 `account_label` 自定义)。
- **Salad / TensorDock 手填余额估算**:这两个平台**无余额查询 API**(已实测 14 个候选端点全 404,官方 Python SDK 也无任何 billing/credit/balance 服务),无法像 Vast(`credit`)/ RunPod(`clientBalance`)那样自动拉取。新增 `balance_usd` 字段(看板配置页可改,保存时自动记录 `balance_asof` 时间戳),看板按当前消耗速率递减,显示「估算余额 $X · 约 Yh 花完」。不知充值/精确计费,会逐渐偏差,需偶尔回填校准。
- **总览内联编辑余额**:无余额 API 的平台(salad/tensordock)在总览卡片余额位置直接点击 ✎ 即可就地填写/修改余额(`$` 前缀输入框 + ✓/✕,回车保存 Esc 取消,编辑时暂停自动刷新),无需再去配置页;复用 `save-platform` 端点(自动盖 `balance_asof`)。Vast/RunPod 余额来自 API,不显示编辑入口。

### Fixed — 修复
- **看板每次刷新卡顿数十秒(性能)**:`salad_live` 对每个容器组**串行**打 2 次 Salad API(实测 10 组 = 22 次串行 ≈ 11.7s/账号,3 个 salad 账号一次构建 >35s),且全在 HTTP 请求线程同步执行;叠加前端每 10s 自动刷新 + 30s 缓存,冷窗口频繁、并发惊群。改为:① 每账号容器组请求用**线程池并发**拉取(11.7s→~0.6s);② 后台 daemon 线程每 30s **预热所有缓存**(salad/余额/矿池),HTTP 请求只读缓存(**serve-stale**,永不阻塞;仅缓存超兜底上限才同步重算)。实测接口 `/api/summary` ~25ms、`/api/rentals` ~0.3s(原 >35s/超时)。
- **dashboard 看不到 Salad 机器(HTTP 400)**:`salad_live` 内层循环把组名变量 `nm` 覆盖成 GPU 名,导致用「RTX 4090 (24 GB)」当组名查 `/instances` 报 400、显示「无在跑机器」。
- **GPU 列显示的是配置而非实际在跑的卡**:改为用矿池 worker 名(= 组名)匹配,显示每台实际 GPU(如 `RTX 4070 Ti SUPER` / `RTX 5070 Ti`)。
- **start-all / stop-all 的 dashboard 进程匹配错误**:`pgrep` 用 `python3 dashboard.py`,而实际进程名为 `Python dashboard.py`,导致重复启动 / 停不掉 dashboard。
- **start-all 误判 Salad「已在运行」而跳过**:旧脚本 `salad_watchdog.py` 进程命令行含 `config.salad.json` 被宽匹配命中;改为精确匹配 `sniper.py --config <文件>`。

### Changed — 变更
- `scripts/start-all.sh`:由「无条件起 4 平台」改为**扫描账号 config、按 `enabled` 启动、按 `api_key_env` 注入 per-account key**;直接调 `python3 sniper.py`(绕过会 `source .env` 覆盖注入 key 的 `run-*.sh`)。
- `scripts/stop-all.sh`:按账号 config 扫描停止(可停掉 `-N` 账号)。
- `.gitignore`:真实平台配置改为通配 `configs/config.*.json` + 保留 `*.example.json`,自动覆盖任意账号数(含钱包的真实 config 始终本地保留)。
- Salad 监控由独立旧脚本 `salad_watchdog.py` **统一迁移到当前项目的 sniper**(同一套 start-all/stop-all/dashboard 管理)。
- `dashboard.py` 后端:新增 `platform_of` / `list_accounts` / `account_label` / `key_var_for` 等 helper;`salad_live` / `platform_balance` 改为 **per-account 缓存**(消除两个同平台账号互相覆盖数据的串号风险);`build_summary/rentals/config/full_config`、`tick_spend` 遍历账号;`prl_address` 不再硬编码读 vast config。

### Configuration / Ops — 配置与运维说明
- 启动采用「做法 B」:未使用的 vast / tensordock 在各自 config 里 `enabled=false`,start-all 自动跳过(不空跑、不抢卡)。
- Salad 监控走「路径 B」(逐实例解析容器日志算力,按 `instance_id` 闭环),门槛由 `gpu_class_names`(Salad class id → 型号)+ `min_hashrate_th`(满载 × 95%)决定;**实例的 reallocate 精确到 `instance_id`,不依赖矿池命名**。
- 注意:Salad 的「暂停租用」是**平台级**(sniper 按平台名读 `control/<平台>.rent-paused`),同平台多个账号会联动。
- 护栏(`max_active_instances` / `max_total_hourly_usd` / `thresholds`)按账号独立计算,最坏总花费 = 各账号上限之和。

---

*本次升级由 Claude (Anthropic · Claude Code) 协助设计与实现 — 2026-06-05*（并非协助）

---

> 以下为 CHANGELOG 建立之前的历史里程碑，据 `docs/plan-archive.md` / `docs/issues.md` 补记，保持变更记录完整。

## [项目首日：抢租核心 + 网页看板 + 开源化 + 稳健性] — 2026-06-04

项目第一天，从零搭出整套系统（M1 抢租核心 → M2 网页看板 → M3 开源化 → M4 稳健性打磨）。

### Added — 新增
- **抢租核心 `sniper.py`**（纯标准库）：Vast.ai / RunPod / TensorDock / Salad 扫描 → 命中价格 & 算力阈值租用 → 监控算力 → 低效/不挖自动销毁拉黑；每平台独立进程 + 独立 `state.<plat>.json`/`logs/<plat>.log` 隔离（`SNIPER_STATE_PATH`/`LOG_PATH`，见 ISS-002）；`--config`/`--live`/`--once` CLI。
- **网页看板 `dashboard.py`**（纯 stdlib `http.server`，:8787）：密码门 + 无状态签名 cookie；总览（钱包/算力/累计租金/待结算·已结算 PEARL/各平台余额 + 预计花完时间）、配置页（公共 + 4 平台二级标签，表单 + raw JSON）、工具链接、后台日志 tail；暂停/恢复租用、重启、关机、改密码；暗色主题、左侧导航。
- 看板**访客(偷窥)模式**（签名 cookie 区分 admin/guest，访客免密码只读）；发光珍珠 **logo + SVG favicon**。
- **Windows(PowerShell)启停脚本** `start-all.ps1` / `stop-all.ps1`（对齐 Linux 版）。
- Salad **按 GPU 型号判健康**（逐实例按 machine_id 从矿池解析型号取 `min_hashrate_th`；`normalize_gpu` 扩 40/50 系列）。
- **开源化**：`.example` 配置模板 + README + `.gitignore`（保护 `.env`/`keys/`/真实 config/state/logs/docs），推公开仓库 `github.com/kuzicode/pearl-wzz-dashboard`（仅 `gpu-sniper-shareable/` 子目录）。

### Fixed — 修复
- Salad **坏实例(完全无算力日志)不被回收**一直烧钱 → 矿池兜底取算力、查不到按 0 计时回收（ISS-010）。
- 看板**全称 GPU key**（`NVIDIA GeForce RTX 5090`）表格空 → 保存用空值覆盖丢失（ISS-010）。
- Salad 踢出门槛「每行型号」不生效 + 24h 计时器掩盖误杀 4070（ISS-009）。
- TensorDock 无算力按 0 回收；RunPod 不挖的 dud pod 回收（ISS-005）。
- Vast 日志 S3 上传竞态 403 重试（ISS-003）；Salad 日志默认 UA 被 WAF 挡 403（ISS-004）；`pkill -f dashboard.py` 自杀（ISS-001）。

### Changed — 变更
- 配置合并到**单一 `.env`**（值单引号转义防注入）；移除 byobu 依赖，改 **nohup/setsid 一键起停**。
- Codex review 修复 5 处（成本护栏盲点 / 回收漏洞 / key 注入等）。
