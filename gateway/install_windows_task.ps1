# 注册 Windows 开机自动启动（工控机部署用，需管理员 PowerShell 执行）
#
#   powershell -ExecutionPolicy Bypass -File gateway\install_windows_task.ps1
#   powershell -ExecutionPolicy Bypass -File gateway\install_windows_task.ps1 -Python "C:\Python311\pythonw.exe"
#
# 卸载：Unregister-ScheduledTask -TaskName SoccerScoreGateway -Confirm:$false
param(
    [string]$TaskName = 'SoccerScoreGateway',
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent

if (-not $Python) {
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($pythonw) { $Python = $pythonw.Source } else { $Python = (Get-Command python.exe).Source }
}
if (-not (Test-Path $Python)) { throw "找不到 Python 解释器：$Python" }
if (-not (Test-Path (Join-Path $RepoRoot 'gateway\config.yaml'))) { throw "缺少 gateway\config.yaml" }

$action = New-ScheduledTaskAction -Execute $Python -Argument 'gateway\main.py' -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description '赛事计分本地网关（串口 -> 云端）' -RunLevel Highest -Force | Out-Null

Write-Host "已注册计划任务 $TaskName"
Write-Host "  解释器：$Python"
Write-Host "  工作目录：$RepoRoot"
Write-Host "  启动方式：开机自动运行 pythonw gateway\main.py（无窗口）"
Write-Host "  日志：$RepoRoot\gateway\logs\gateway.log"
