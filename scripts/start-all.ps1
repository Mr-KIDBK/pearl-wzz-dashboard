# 一条命令后台启动全部服务(Windows / PowerShell): 4 平台 sniper + 网页看板。输出到 logs\。
# 运行: powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
#   或: 右键本文件 -> 使用 PowerShell 运行
# 停止: powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# 载入 .env 到当前进程环境(子进程继承; 看板/平台 API key 需要)
if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.*)\s*$') {
      [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
  }
}

function Test-Running([string]$pattern) {
  $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='python3.exe'" -ErrorAction SilentlyContinue
  foreach ($p in $procs) { if ($p.CommandLine -and $p.CommandLine -like "*$pattern*") { return $true } }
  return $false
}

function Start-Sniper([string]$name) {
  if (Test-Running "config.$name.json") { Write-Host "  [skip] $name 已在运行, 跳过"; return }
  # 各平台独立 state/log(与 Linux 版一致, 防多进程串 state)
  $env:SNIPER_LOG_PATH   = "logs\$name.log"
  $env:SNIPER_STATE_PATH = "state.$name.json"
  $cmd = "& '$Root\scripts\run-$name.ps1' -Live *>> '$Root\logs\$name.log'"
  Start-Process -WindowStyle Hidden -WorkingDirectory $Root -FilePath "powershell" `
    -ArgumentList "-ExecutionPolicy","Bypass","-NoProfile","-Command",$cmd | Out-Null
  Write-Host "  [ok] $name 启动 -> logs\$name.log"
}

Write-Host "启动平台 sniper(live 抢卡):"
foreach ($p in @("vast","runpod","tensordock")) { Start-Sniper $p }

# Salad 仅在 config.salad.json 的 salad.enabled=true 时启动
$saladOn = $false
if (Test-Path "configs\config.salad.json") {
  try {
    python -c "import json,sys;sys.exit(0 if json.load(open('configs/config.salad.json')).get('salad',{}).get('enabled') else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $saladOn = $true }
  } catch { }
}
if ($saladOn) { Start-Sniper "salad" } else { Write-Host "  [skip] salad 未启用(config.salad.json 的 salad.enabled=false), 跳过" }

# 清掉平台专用 env, 避免看板进程继承
Remove-Item Env:SNIPER_LOG_PATH   -ErrorAction SilentlyContinue
Remove-Item Env:SNIPER_STATE_PATH -ErrorAction SilentlyContinue

# 网页看板
Write-Host "启动网页看板:"
if (Test-Running "dashboard.py") {
  Write-Host "  [skip] dashboard 已在运行"
} else {
  Start-Process -WindowStyle Hidden -WorkingDirectory $Root -FilePath "powershell" `
    -ArgumentList "-ExecutionPolicy","Bypass","-NoProfile","-Command","python .\dashboard.py *>> '$Root\logs\dashboard.log'" | Out-Null
  Write-Host "  [ok] dashboard 启动 -> logs\dashboard.log"
}

$port = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { "8787" }
Write-Host ""
Write-Host "[OK] 全部启动完成。"
Write-Host "   网页看板: http://localhost:$port  (登录 admin / .env 里的 DASHBOARD_PASSWORD)"
Write-Host "   看日志:   Get-Content logs\<平台>.log -Wait   或在看板「配置」页点「查看后台日志」"
Write-Host "   全部停止: powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1"
