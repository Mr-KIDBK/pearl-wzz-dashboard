# 一条命令停止全部服务(Windows / PowerShell): 4 平台 sniper + 网页看板。
# 运行: powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$stopped = 0

# 只在 python 进程里按命令行匹配, 本脚本由 powershell.exe 运行, 不会误杀自身
function Stop-ByPattern([string]$label, [string]$pattern) {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='python3.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pattern*" })
  if ($procs.Count -gt 0) {
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "  [stop] $label 已停 (pid $($procs.ProcessId -join ', '))"
    return $true
  }
  return $false
}

foreach ($name in @("vast","runpod","tensordock","salad")) {
  if (Stop-ByPattern $name "config.$name.json") { $stopped = 1 }
}
if (Stop-ByPattern "dashboard" "dashboard.py") { $stopped = 1 }

if ($stopped -eq 0) { Write-Host "没有在运行的服务" } else { Write-Host "[OK] 全部已停止" }
