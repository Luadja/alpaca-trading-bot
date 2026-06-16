# Keep the trading bot + watchdog alive on Windows, restarting the bot if it crashes.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1
# Stop with Ctrl+C. For survive-logout/reboot, register this with Task Scheduler (see README).

param(
    [int]$Interval = 300,        # bot trade-cycle seconds
    [int]$RestartDelaySec = 10   # wait before restarting after a crash
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fall back to PATH python

Write-Host "Project: $root"
$wd = $null
try {
    while ($true) {
        # (Re)launch the watchdog if it isn't running, so the dead-man's switch is always up.
        if ($null -eq $wd -or $wd.HasExited) {
            Write-Host "$(Get-Date -Format o)  (re)starting watchdog..."
            $wd = Start-Process -FilePath $py -ArgumentList "-m", "scripts.watchdog" -WorkingDirectory $root -PassThru
        }
        Write-Host "$(Get-Date -Format o)  starting bot (interval ${Interval}s)..."
        & $py -m bot.run --interval $Interval
        Write-Host "$(Get-Date -Format o)  bot exited (code $LASTEXITCODE) - restarting in ${RestartDelaySec}s (Ctrl+C to stop)."
        Start-Sleep -Seconds $RestartDelaySec
    }
}
finally {
    # Don't leave the watchdog orphaned when the launcher stops.
    if ($wd -and -not $wd.HasExited) {
        Write-Host "Stopping watchdog (pid $($wd.Id))..."
        Stop-Process -Id $wd.Id -Force -ErrorAction SilentlyContinue
    }
}
