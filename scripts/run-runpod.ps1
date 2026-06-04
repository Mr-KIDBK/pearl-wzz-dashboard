param([switch]$Live, [switch]$Once)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.*)\s*$') {
      [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
  }
}

$ArgsList = @("--config", "configs/config.runpod.json")
if ($Live) { $ArgsList += "--live" }
if ($Once) { $ArgsList += "--once" }
python .\sniper.py @ArgsList
